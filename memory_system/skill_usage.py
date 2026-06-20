#!/usr/bin/env python3
"""cc-session-memory · skill 调用流水（hooks/summarize.py 与 cli/ccmem.py 共用）

数据文件：<memories_dir>/skill_usage.jsonl，一行一次调用：
  {"key": "...", "ts": "2026-06-10T14:32:01+09:00", "date": "2026-06-10",
   "session_id": "...", "cwd": "/Users/...", "skill": "sess", "source": "tool"}

source：
  · "tool"  — 模型主动调 Skill 工具（transcript 里 tool_use name=="Skill"）
  · "slash" — 用户手敲 slash command（user 消息里的 <command-name> 标签）
  · "mcp"   — MCP 工具调用（tool_use name 以 "mcp__" 开头，skill 字段存完整工具名）

key 全局唯一去重键：tool_use 用 CC 自带的 toolu_ id；slash 用 "<消息uuid>:<skill>"。
Stop hook 实时记录与 transcript 历史回填走同一套 key → 混用不会重复计数。
此文件不参与 memories/*.md 的 size-cap 剪枝，也不受 CC cleanupPeriodDays 影响，长期累积。
"""

from __future__ import annotations

import fcntl
import json
import re
from pathlib import Path
from typing import Any

USAGE_FILENAME = "skill_usage.jsonl"

CMD_RE = re.compile(r"<command-name>/?([\w.:一-鿿-]+)</command-name>")

# CC 内置 CLI 命令也会以 <command-name> 形式出现在 transcript 里，不是 skill。
# 展示时默认过滤（--all 可看全部）；记录时不过滤，原始数据保真。
BUILTIN_COMMANDS = {
    "clear", "exit", "quit", "login", "logout", "help", "status", "cost",
    "compact", "resume", "doctor", "bug", "memory", "permissions", "hooks",
    "export", "agents", "ide", "usage", "context", "rewind", "statusline",
    "bashes", "add-dir", "todos", "upgrade", "release-notes",
    "privacy-settings", "vim", "terminal-setup", "install-github-app",
    "output-style", "config", "mcp", "model", "effort", "goal", "plugin",
    "extra-usage", "chrome", "fast", "migrate-installer", "approved-tools",
}


def usage_path(memories_dir: Path) -> Path:
    return memories_dir / USAGE_FILENAME


def extract_events(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 transcript JSONL 行对象里抽出 skill 调用事件（不去重，去重在 append）。"""
    events: list[dict[str, Any]] = []
    for obj in lines:
        if not isinstance(obj, dict):
            continue
        typ = obj.get("type")
        if typ not in ("user", "assistant"):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        ts = str(obj.get("timestamp") or "")
        base = {
            "ts": ts,
            "date": ts[:10],
            "session_id": str(obj.get("sessionId") or ""),
            "cwd": str(obj.get("cwd") or ""),
        }
        uuid = str(obj.get("uuid") or "")
        if typ == "assistant" and isinstance(content, list):
            for item in content:
                if not (isinstance(item, dict) and item.get("type") == "tool_use"):
                    continue
                name = item.get("name")
                if name == "Skill":
                    inp = item.get("input")
                    skill = str(inp.get("skill") or "?") if isinstance(inp, dict) else "?"
                    events.append({**base, "skill": skill, "source": "tool",
                                   "key": str(item.get("id") or f"{uuid}:{skill}")})
                elif isinstance(name, str) and name.startswith("mcp__"):
                    events.append({**base, "skill": name, "source": "mcp",
                                   "key": str(item.get("id") or f"{uuid}:{name}")})
        elif typ == "user":
            texts: list[str] = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
            for t in texts:
                for skill in CMD_RE.findall(t):
                    events.append({**base, "skill": skill, "source": "slash",
                                   "key": f"{uuid}:{skill}"})
    return events


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("skill"):
                out.append(obj)
    return out


def append_events(path: Path, events: list[dict[str, Any]]) -> int:
    """按 key 去重后追加，返回实际写入条数。带 fcntl 排它锁。"""
    if not events:
        return 0
    seen = {e.get("key") for e in load_events(path)}
    fresh: list[dict[str, Any]] = []
    for e in events:
        k = e.get("key")
        if k and k not in seen:
            seen.add(k)
            fresh.append(e)
    if not fresh:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in fresh)
    with path.open("ab") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(payload.encode("utf-8"))
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return len(fresh)
