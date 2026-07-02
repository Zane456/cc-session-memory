#!/usr/bin/env python3
"""cc-session-memory 核心纯函数单测（pytest）。

覆盖：frontmatter 解析、cwd 匹配、轮次切分、LLM 输出解析、
transcript 单轮抽取、skill 事件抽取与去重追加、容量剪枝（含 .lock 清理）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# summarize 导入时会在 CC_SESSION_MEMORY_HOME 下建 logs/，测试重定向到临时目录
os.environ.setdefault("CC_SESSION_MEMORY_HOME", tempfile.mkdtemp(prefix="ccmem-test-"))
sys.path.insert(0, str(ROOT / "memory_system"))
sys.path.insert(0, str(ROOT / "memory_system" / "hooks"))
sys.path.insert(0, str(ROOT / "memory_system" / "cli"))

import ccmem  # noqa: E402
import skill_usage  # noqa: E402
import summarize  # noqa: E402


# ─── ccmem：frontmatter / cwd / 轮次切分 ─────────────────────────────────────

def test_parse_frontmatter_scalars_and_list(tmp_path):
    p = tmp_path / "m.md"
    p.write_text(
        "---\n"
        "session_id: abc-123\n"
        "date: 2026-05-09\n"
        "files_modified:\n"
        "  - \"a.py\"\n"
        "  - b.py\n"
        "---\n\n# body\n",
        encoding="utf-8",
    )
    fm = ccmem.parse_frontmatter(p)
    assert fm["session_id"] == "abc-123"
    assert fm["date"] == "2026-05-09"
    assert fm["files_modified"] == ["a.py", "b.py"]


def test_parse_frontmatter_missing_or_broken(tmp_path):
    p = tmp_path / "no-fm.md"
    p.write_text("# 没有 frontmatter\n", encoding="utf-8")
    assert ccmem.parse_frontmatter(p) == {}


def test_cwd_matches():
    assert ccmem._cwd_matches("/a/b", "/a/b")
    assert ccmem._cwd_matches("/a/b/c", "/a/b")   # memory 在 query 子目录
    assert ccmem._cwd_matches("/a/b", "/a/b/c")   # query 在 memory 子目录
    assert not ccmem._cwd_matches("/a/bc", "/a/b")  # 前缀但不是路径边界
    assert not ccmem._cwd_matches("", "/a/b")


def test_split_and_filter_sections():
    text = (
        "---\nsession_id: x\n---\n\n# 标题\n\n"
        "## 轮次 1 · 10:00:00\n**用户**：修 bug\n**关键词**：pytest\n\n"
        "## 轮次 2 · 10:05:00\n**用户**：写文档\n**关键词**：README\n\n"
    )
    preamble, sections = ccmem.split_sections(text)
    assert "# 标题" in preamble
    assert len(sections) == 2
    import re
    hits = ccmem.filter_matching_sections(sections, re.compile("pytest"))
    assert len(hits) == 1
    assert "轮次 1" in hits[0][0]


# ─── summarize：LLM 输出解析 / 单轮抽取 ──────────────────────────────────────

def test_parse_turn_summary_plain_and_bold():
    plain = "用户: 修 bug\n结果: 改了 utils.py\n关键词: bug, utils.py"
    out = summarize.parse_turn_summary(plain)
    assert out == {"user": "修 bug", "result": "改了 utils.py", "keywords": "bug, utils.py"}

    bold = "**用户**：修 bug\n- **结果**：改了 utils.py\n**关键词**：bug"
    out2 = summarize.parse_turn_summary(bold)
    assert out2["user"] == "修 bug"
    assert out2["result"] == "改了 utils.py"
    assert out2["keywords"] == "bug"


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")


def test_extract_last_turn(tmp_path):
    t = tmp_path / "s.jsonl"
    _write_transcript(t, [
        {"type": "user", "uuid": "u0", "timestamp": "2026-05-09T10:00:00Z",
         "message": {"role": "user", "content": "旧问题"}},
        {"type": "user", "uuid": "u1", "timestamp": "2026-05-09T10:01:00Z",
         "message": {"role": "user", "content": "帮我修 utils.py 的 bug"}},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-05-09T10:01:10Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "我先看下文件"},
             {"type": "tool_use", "id": "toolu_01", "name": "Read",
              "input": {"file_path": "/proj/utils.py"}},
         ]}},
        {"type": "user", "uuid": "u2", "timestamp": "2026-05-09T10:01:20Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_01", "content": "..."}]}},
        {"type": "assistant", "uuid": "a2", "timestamp": "2026-05-09T10:01:30Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_02", "name": "Edit",
              "input": {"file_path": "/proj/utils.py"}},
             {"type": "text", "text": "修好了，是个 off-by-one"},
         ]}},
    ])
    turn = summarize.extract_last_turn(str(t))
    assert turn["user_text"] == "帮我修 utils.py 的 bug"
    assert turn["modified"] == ["/proj/utils.py"]
    assert turn["read"] == []  # 同文件先读后改 → 只算 modify
    assert "off-by-one" in turn["assistant_text"]


def test_extract_last_turn_missing_transcript():
    turn = summarize.extract_last_turn("/nonexistent/x.jsonl", last_assistant_from_payload="fallback")
    assert turn["assistant_text"] == "fallback"
    assert turn["modified"] == []


# ─── skill_usage：事件抽取 / 锁内去重追加 ────────────────────────────────────

def _fixture_lines() -> list[dict]:
    return [
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-06-10T14:00:00+09:00",
         "sessionId": "sess-1", "cwd": "/proj",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_s1", "name": "Skill", "input": {"skill": "sess"}},
             {"type": "tool_use", "id": "toolu_m1", "name": "mcp__tavily__tavily_search",
              "input": {"query": "x"}},
         ]}},
        {"type": "user", "uuid": "u1", "timestamp": "2026-06-10T14:01:00+09:00",
         "sessionId": "sess-1", "cwd": "/proj",
         "message": {"role": "user", "content": "<command-name>/ccskill</command-name>"}},
    ]


def test_extract_events():
    events = skill_usage.extract_events(_fixture_lines())
    by_source = {e["source"]: e for e in events}
    assert by_source["tool"]["skill"] == "sess"
    assert by_source["tool"]["key"] == "toolu_s1"
    assert by_source["mcp"]["skill"] == "mcp__tavily__tavily_search"
    assert by_source["slash"]["skill"] == "ccskill"
    assert len(events) == 3


def test_append_events_dedupe(tmp_path):
    path = tmp_path / "skill_usage.jsonl"
    events = skill_usage.extract_events(_fixture_lines())
    assert skill_usage.append_events(path, events) == 3
    # 重复追加 → 全部按 key 去重
    assert skill_usage.append_events(path, events) == 0
    # 混入 1 条新事件 → 只写新那条
    extra = dict(events[0], key="toolu_new")
    assert skill_usage.append_events(path, events + [extra]) == 1
    assert len(skill_usage.load_events(path)) == 4


# ─── enforce_size_cap：FIFO 剪枝 + lock 清理 ─────────────────────────────────

def _make_memory(d: Path, name: str, day: int, size: int = 2000) -> Path:
    p = d / name
    fm = f"---\nsession_id: s-{day:02d}\ntimestamp: 2026-01-{day:02d}T00:00:00+00:00\n---\n"
    p.write_text(fm + "x" * max(0, size - len(fm)), encoding="utf-8")
    return p


def test_enforce_size_cap_prunes_oldest_and_locks(tmp_path):
    for day in range(1, 16):  # 15 个文件 × ~2KB，时间升序
        p = _make_memory(tmp_path, f"2026-01-{day:02d}-s{day:02d}.md", day)
        (tmp_path / (p.name + ".lock")).touch()  # 每个都有 sidecar 锁
    orphan = tmp_path / "2025-12-31-dead.md.lock"  # 孤儿锁：md 已不存在
    orphan.touch()

    result = summarize.enforce_size_cap(tmp_path, 0.01)  # 上限 ~10.5KB

    assert result["pruned"] == 5          # 删最旧 5 个
    assert result["kept"] == 10           # MIN_KEEP_NEWEST=10 受保护
    remaining = sorted(f.name for f in tmp_path.glob("*.md"))
    assert remaining[0].startswith("2026-01-06")  # 01-01 ~ 01-05 已删
    # 被剪的 md 的 sidecar 锁一并删除；保留的 md 锁不动；孤儿锁被清扫
    assert not (tmp_path / "2026-01-01-s01.md.lock").exists()
    assert (tmp_path / "2026-01-15-s15.md.lock").exists()
    assert not orphan.exists()


def test_enforce_size_cap_disabled_and_under_cap(tmp_path):
    _make_memory(tmp_path, "2026-01-01-s01.md", 1)
    assert summarize.enforce_size_cap(tmp_path, 0)["pruned"] == 0     # 0 = 关闭
    assert summarize.enforce_size_cap(tmp_path, 200)["pruned"] == 0   # 远未超限


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
