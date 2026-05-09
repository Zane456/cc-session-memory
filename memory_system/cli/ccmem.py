#!/usr/bin/env python3
"""cc-memory CLI · 检索本地会话记忆

用法：
  ccmem list                       列最近 20 条
  ccmem list --all                 列全部
  ccmem list --date 2026-05        按日期前缀过滤
  ccmem here [-n N]                列当前目录（项目）范围内的最近 N 条
  ccmem here --cwd /some/path      指定目录
  ccmem search "关键词"              全文正则搜索（全局）
  ccmem search "关键词" --here       搜索叠加 cwd 过滤（限当前项目）
  ccmem search "关键词" --cwd /x     搜索叠加任意目录过滤
  ccmem show <id-or-prefix>        打印一条记忆
  ccmem latest [-n N]              打印最新 N 条全文
  ccmem path                       打印 memories 目录路径

cwd 匹配规则（用于 here / --here / find / last-session）：
  · 严格相等
  · 一方是另一方的子目录（项目内任意位置都算"在这个项目里"）
  · 路径在比较前会 expand + resolve 软链接

环境变量：
  CC_MEMORY_DIR     覆盖 memories 目录
  CC_MEMORY_CONFIG  覆盖配置文件路径
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


def _load_config() -> dict:
    cfg_path = Path(os.environ.get("CC_MEMORY_CONFIG", str(Path.home() / ".config" / "cc-memory" / "config.json")))
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _config_memories_dir() -> Path | None:
    cfg = _load_config()
    if cfg.get("memories_dir"):
        return Path(cfg["memories_dir"])
    return None


def _config_cap_mb() -> int:
    cfg = _load_config()
    try:
        return int(cfg.get("max_db_size_mb", 200))
    except Exception:
        return 200


def memories_dir() -> Path:
    if env := os.environ.get("CC_MEMORY_DIR"):
        return Path(env)
    if d := _config_memories_dir():
        return d
    # 默认：脚本上两级的 memories/
    return Path(__file__).resolve().parents[2] / "memories"


def iter_memories() -> Iterable[Path]:
    d = memories_dir()
    if not d.exists():
        return []
    return sorted(d.glob("*.md"), reverse=True)


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Parse YAML-ish frontmatter. Supports scalars and indented `- item` lists.
    Returns dict[str, str | list[str]]."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in text[3:end].splitlines():
        if not line.strip():
            current_list_key = None
            continue
        # 缩进的列表项："  - value"
        if line.startswith((" ", "\t")) and line.lstrip().startswith("- "):
            if current_list_key:
                item = line.lstrip()[2:].strip()
                # 去掉两端引号（如果是 quoted YAML 字符串）
                if (item.startswith('"') and item.endswith('"')) or (item.startswith("'") and item.endswith("'")):
                    item = item[1:-1]
                if not isinstance(fm.get(current_list_key), list):
                    fm[current_list_key] = []
                fm[current_list_key].append(item)
            continue
        # 顶层 "key: value"
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if v == "[]":
                fm[k] = []
                current_list_key = None
            elif v == "":
                fm[k] = []          # 占位；如果后面无缩进项就保持空列表
                current_list_key = k
            else:
                fm[k] = v
                current_list_key = None
    return fm


# ────────────────────────────────────────────────────────────────────────────
# cwd 匹配
# ────────────────────────────────────────────────────────────────────────────

def _normalize_path(p: str) -> str:
    """Expand `~`，解软链，去尾斜杠。失败时退回原字符串。"""
    if not p:
        return ""
    try:
        return str(Path(p).expanduser().resolve())
    except Exception:
        return p.rstrip("/")


def _cwd_matches(memory_cwd: str, query_cwd: str) -> bool:
    """记忆的 cwd 与查询 cwd 是否属于"同一项目"——
    相等 / memory 是 query 的子目录 / query 是 memory 的子目录 都算命中。"""
    if not memory_cwd or not query_cwd:
        return False
    m = _normalize_path(memory_cwd)
    q = _normalize_path(query_cwd)
    if not m or not q:
        return False
    if m == q:
        return True
    sep = os.sep
    if m.startswith(q + sep):
        return True
    if q.startswith(m + sep):
        return True
    return False


_SECTION_HEADER_RE = re.compile(r"^##\s*轮次\s+\d+[^\n]*$", re.M)


def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """按 ^## 轮次 N 切分 markdown。
    返回 (preamble, [(header_line, body), ...])。
    preamble 是首个轮次 header 之前的内容（含 frontmatter + 任何引言）。
    body 不含 header 自身那行。"""
    matches = list(_SECTION_HEADER_RE.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        header = m.group(0)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        out.append((header, body))
    return preamble, out


def filter_matching_sections(
    sections: list[tuple[str, str]], pattern: re.Pattern[str]
) -> list[tuple[str, str]]:
    """只保留 header 或 body 命中 pattern 的段。"""
    return [(h, b) for h, b in sections if pattern.search(h) or pattern.search(b)]


def _make_byte_budget(max_bytes: int):
    """返回 (writer, state)：writer 每次写入累计字节数；state['exceeded'] 在超出后变 True。
    max_bytes <= 0 视为无限。"""
    state = {"bytes": 0, "exceeded": False, "limit": max_bytes}

    def writer(s: str = "") -> None:
        line = s + "\n"
        state["bytes"] += len(line.encode("utf-8"))
        sys.stdout.write(line)
        if max_bytes > 0 and state["bytes"] >= max_bytes:
            state["exceeded"] = True

    return writer, state


# ────────────────────────────────────────────────────────────────────────────
# CC 原始 transcript 检索（--raw 模式）
# memories/ 里只是 GLM 摘要；CC 自己把每个 session 的完整对话写在
# ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl，要看原话就读这个。
# ────────────────────────────────────────────────────────────────────────────

CC_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _find_jsonl_for_session(session_id: str) -> Path | None:
    if not session_id or not CC_PROJECTS_DIR.is_dir():
        return None
    for p in CC_PROJECTS_DIR.rglob(f"{session_id}.jsonl"):
        if p.is_file():
            return p
    return None


def _extract_jsonl_message_parts(content: Any) -> tuple[str, list[str], list[str]]:
    """从 jsonl 的 message.content 抽 (text, tool_uses, tool_results)。"""
    if content is None:
        return "", [], []
    if isinstance(content, str):
        return content.strip(), [], []
    if not isinstance(content, list):
        return str(content), [], []
    texts: list[str] = []
    tool_uses: list[str] = []
    tool_results: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "text" and isinstance(item.get("text"), str):
            texts.append(item["text"])
        elif t == "tool_use":
            name = item.get("name", "?")
            inp = item.get("input") or {}
            hint = ""
            if isinstance(inp, dict):
                for k in ("file_path", "path", "notebook_path", "command", "pattern", "query"):
                    v = inp.get(k)
                    if isinstance(v, str) and v:
                        hint = v[:120]
                        break
            tool_uses.append(f"{name}: {hint}" if hint else name)
        elif t == "tool_result":
            tc = item.get("content")
            if isinstance(tc, list):
                tc = next(
                    (x.get("text", "") for x in tc
                     if isinstance(x, dict) and x.get("type") == "text"),
                    "",
                )
            if isinstance(tc, str) and tc:
                tool_results.append(tc[:240])
    return "\n".join(texts).strip(), tool_uses, tool_results


def iter_jsonl_messages(path: Path):
    """yield {role, ts, text, tool_uses, tool_results, blob} for user/assistant lines."""
    try:
        f = path.open(encoding="utf-8")
    except OSError:
        return
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind not in ("user", "assistant"):
                continue
            msg = obj.get("message") or {}
            if not isinstance(msg, dict):
                continue
            text, tool_uses, tool_results = _extract_jsonl_message_parts(msg.get("content"))
            if not text and not tool_uses and not tool_results:
                continue
            blob_parts = [text] + tool_uses + tool_results
            yield {
                "role": (msg.get("role") or kind),
                "ts": obj.get("timestamp") or "",
                "text": text,
                "tool_uses": tool_uses,
                "tool_results": tool_results,
                "blob": "\n".join(blob_parts),
            }


def _render_jsonl_message(m: dict[str, Any], include_tool_results: bool = False) -> str:
    ts = (m["ts"] or "")[:19].replace("T", " ")
    role = str(m["role"]).upper()
    lines = [f"[{role} {ts}]"]
    if m["text"]:
        lines.append(m["text"])
    for tu in m["tool_uses"]:
        lines.append(f"  [tool: {tu}]")
    if include_tool_results:
        for tr in m["tool_results"][:2]:
            tr_short = tr.replace("\n", " ⏎ ")[:240]
            lines.append(f"  [tool_result: {tr_short}]")
    return "\n".join(lines)


def _render_session_raw(
    fm: dict[str, Any],
    md_path: Path,
    write,
    state,
    query: re.Pattern[str] | None = None,
    include_tool_results: bool = False,
) -> bool:
    """Render one session's CC raw transcript through byte-budget writer.
    Returns True if anything got printed (jsonl found and at least one message
    survived filter); False otherwise."""
    sid = fm.get("session_id")
    sid = sid if isinstance(sid, str) else ""
    cwd = fm.get("cwd") if isinstance(fm.get("cwd"), str) else ""
    line = "=" * 40
    write(line)
    write(f"📌 CC 原始 transcript · session={sid[:8]}")
    write(line)
    write(f"Session ID: {sid}")
    write(f"cwd: {cwd}")
    write(f"摘要 md: {md_path}")
    if not sid:
        write("⚠️  frontmatter 没有 session_id，跳过")
        return False
    jsonl = _find_jsonl_for_session(sid)
    if jsonl is None:
        write(f"⚠️  ~/.claude/projects/ 没找到 {sid}.jsonl（可能已被剪枝）")
        return False
    write(f"jsonl: {jsonl}")
    write()

    rendered = 0
    for m in iter_jsonl_messages(jsonl):
        if state["exceeded"]:
            break
        if query is not None and not query.search(m["blob"]):
            continue
        write(_render_jsonl_message(m, include_tool_results=include_tool_results))
        write()
        rendered += 1

    if rendered == 0:
        if query is not None:
            write("(no match in this session's raw transcript)")
        else:
            write("(jsonl is empty or has no user/assistant messages)")
    else:
        write(line)
        write(f"✅ 共 {rendered} 条 user/assistant 消息" +
              (f"（命中关键词）" if query is not None else ""))
        write(line)
    return rendered > 0


def _print_summary_line(p: Path) -> None:
    fm = parse_frontmatter(p)
    sid = (fm.get("session_id") or "?")
    sid = sid[:8] if isinstance(sid, str) else "?"
    d = fm.get("date") if isinstance(fm.get("date"), str) else "?"
    # 时间字段优先 last_update（新模板），回退 time / start_time（中/旧模板）
    t = ""
    for k in ("last_update", "time", "start_time"):
        v = fm.get(k)
        if isinstance(v, str) and v:
            t = v
            break
    # turn 数：turns_recorded（新）→ turns（旧）
    turns = "?"
    for k in ("turns_recorded", "turns"):
        v = fm.get(k)
        if isinstance(v, str) and v:
            turns = v
            break
    # 文件数：旧模板有 files_modified frontmatter；新模板 per-turn 不放 frontmatter
    files_str = ""
    mod = fm.get("files_modified")
    if isinstance(mod, list) and mod:
        files_str = f"  files={len(mod)}"
    topic = _first_topic(p)
    print(f"{d} {t}  [{sid}]  turns={turns}{files_str}  {topic}")
    print(f"    {p}")


def cmd_list(args: argparse.Namespace) -> int:
    items = list(iter_memories())
    if args.date:
        items = [p for p in items if p.name.startswith(args.date)]
    limit = None if args.all else args.limit
    if limit:
        items = items[:limit]
    if not items:
        print("(no memories yet)")
        return 0
    for p in items:
        _print_summary_line(p)
    return 0


def cmd_here(args: argparse.Namespace) -> int:
    target = _normalize_path(args.cwd or os.getcwd())
    if not target:
        print("(cannot determine cwd)", file=sys.stderr)
        return 2
    items: list[Path] = []
    for p in iter_memories():
        fm = parse_frontmatter(p)
        memory_cwd = fm.get("cwd")
        if isinstance(memory_cwd, str) and _cwd_matches(memory_cwd, target):
            items.append(p)
    limit = None if args.all else args.limit
    if limit:
        items = items[:limit]
    print(f"# memories scoped to: {target}")
    if not items:
        print("(no memories in this directory)")
        return 0
    for p in items:
        _print_summary_line(p)
    return 0


def _first_topic(path: Path) -> str:
    """提取首屏可显示的一行预览。三种模板都兼容：
    · 新（per-turn append）：'## 轮次 1 ...\\n**用户**：xxx'
    · 中（session-level）：'## 用户提问\\n<内容>'
    · 旧：'## 主题\\n<内容>'
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    # 新模板：取第 1 个轮次段里的"**用户**："那行
    m = re.search(r"^##\s*轮次\s+\d+[^\n]*\n\*\*用户\*\*[：:]\s*(.+)$", text, re.M)
    if m:
        return m.group(1).strip()[:80]
    # 中/旧模板
    for heading in ("用户提问", "主题"):
        m = re.search(rf"^##\s*{heading}\s*\n(.+?)(?:\n##|\Z)", text, re.S | re.M)
        if m:
            return m.group(1).strip().split("\n")[0][:80]
    return ""


def cmd_search(args: argparse.Namespace) -> int:
    pattern = re.compile(args.query, re.IGNORECASE | re.MULTILINE)

    target_cwd: str | None = None
    if getattr(args, "here", False):
        target_cwd = _normalize_path(os.getcwd())
    elif getattr(args, "cwd", None):
        target_cwd = _normalize_path(args.cwd)

    if target_cwd:
        print(f"# search scoped to: {target_cwd}")

    hits = 0
    skipped_cwd = 0
    for p in iter_memories():
        if target_cwd:
            fm = parse_frontmatter(p)
            mc = fm.get("cwd")
            if not (isinstance(mc, str) and _cwd_matches(mc, target_cwd)):
                skipped_cwd += 1
                continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        hits += 1
        fm = parse_frontmatter(p)
        d = fm.get("date") if isinstance(fm.get("date"), str) else "?"
        t = fm.get("time") if isinstance(fm.get("time"), str) else ""
        sid_v = fm.get("session_id")
        sid = sid_v[:8] if isinstance(sid_v, str) else "?"
        print(f"\n=== {d} {t}  [{sid}]  {p.name} ===")
        for m in matches[: args.context]:
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 100)
            snippet = text[start:end].replace("\n", " ⏎ ")
            print(f"  …{snippet}…")
        if args.limit and hits >= args.limit:
            break
    if hits == 0:
        scope = f" (scoped to {target_cwd}; {skipped_cwd} memories outside scope)" if target_cwd else ""
        print(f"(no match for {args.query!r}{scope})")
        return 1
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    target = _resolve(args.id_or_prefix)
    if not target:
        print(f"not found: {args.id_or_prefix}", file=sys.stderr)
        return 1
    print(target.read_text(encoding="utf-8"))
    return 0


def _resolve(needle: str) -> Path | None:
    """支持完整文件名、session_id 前缀、日期前缀。"""
    d = memories_dir()
    candidates = list(d.glob(f"{needle}*"))
    if candidates:
        return candidates[0]
    for p in iter_memories():
        fm = parse_frontmatter(p)
        sid = fm.get("session_id", "")
        if sid.startswith(needle):
            return p
    return None


def cmd_path(_: argparse.Namespace) -> int:
    print(memories_dir())
    return 0


def _format_size(n: int) -> str:
    return f"{n/1024/1024:.1f} MB" if n >= 1024*1024 else f"{n/1024:.1f} KB" if n >= 1024 else f"{n} B"


def cmd_stats(args: argparse.Namespace) -> int:
    """显示 memories 总览：条数、占用、最早/最新、上限百分比。"""
    cap_mb = _config_cap_mb()
    md = memories_dir()
    items = list(md.glob("*.md"))
    total = sum(p.stat().st_size for p in items if p.is_file())
    if not items:
        print(f"memories dir : {md}")
        print(f"总条数       : 0")
        print(f"上限         : {cap_mb} MB")
        return 0

    timestamps: list[str] = []
    for p in items:
        fm = parse_frontmatter(p)
        ts = fm.get("timestamp") if isinstance(fm.get("timestamp"), str) else ""
        if not ts:
            ts = fm.get("date") if isinstance(fm.get("date"), str) else ""
        if ts:
            timestamps.append(ts)
    timestamps.sort()
    oldest = timestamps[0] if timestamps else "?"
    newest = timestamps[-1] if timestamps else "?"

    cap_bytes = cap_mb * 1024 * 1024 if cap_mb > 0 else 0
    pct = (total / cap_bytes * 100) if cap_bytes else 0
    warn = ""
    if cap_bytes:
        if total > cap_bytes:
            warn = "  ⚠️ 已超上限！下次写入时会自动剪枝"
        elif pct >= 80:
            warn = "  ⚠️ 接近上限"

    print(f"memories dir : {md}")
    print(f"总条数        : {len(items)}")
    if cap_mb > 0:
        print(f"总占用        : {_format_size(total)} / {cap_mb} MB ({pct:.0f}%){warn}")
    else:
        print(f"总占用        : {_format_size(total)}（上限已禁用）")
    print(f"最早一条      : {oldest}")
    print(f"最新一条      : {newest}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """手动触发剪枝，可临时覆盖上限。"""
    cap_mb = args.max_size if args.max_size is not None else _config_cap_mb()
    if cap_mb <= 0:
        print("max_db_size_mb is 0 (disabled)；用 --max-size <MB> 指定一个临时上限再跑")
        return 1
    md = memories_dir()
    # 调用 summarize.py 里的 enforce_size_cap
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
    try:
        from summarize import enforce_size_cap  # type: ignore
    except Exception as e:
        print(f"cannot import enforce_size_cap: {e}", file=sys.stderr)
        return 2

    result = enforce_size_cap(md, cap_mb, log_func=print)
    if result["pruned"] == 0:
        before = result.get("before_bytes", 0)
        if before <= cap_mb * 1024 * 1024:
            print(f"no pruning needed (current: {_format_size(before)} / {cap_mb} MB)")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    """关键词搜索 + 拼出命中 session 的完整 markdown，按 timestamp 降序。
    --section-only：只输出命中关键词的 ## 轮次 段，省略其余轮次。
    --max-bytes N：跨 session 累计输出预算，超出后停止并提示。"""
    try:
        pattern = re.compile(args.query, re.IGNORECASE | re.MULTILINE)
    except re.error as e:
        print(f"(invalid regex: {e}; falling back to literal search)")
        pattern = re.compile(re.escape(args.query), re.IGNORECASE | re.MULTILINE)

    target_cwd = None if args.all_ else _normalize_path(os.getcwd())
    raw_mode = bool(getattr(args, "raw", False))

    # raw 模式：不依赖摘要内容命中——把 cwd 范围内全部 session 都喂进去，
    # 让 _render_session_raw 在 jsonl 内部 grep。
    pairs: list[tuple[Path, dict[str, Any], str]] = []
    for p in iter_memories():
        fm = parse_frontmatter(p)
        if target_cwd:
            mc = fm.get("cwd")
            if not (isinstance(mc, str) and _cwd_matches(mc, target_cwd)):
                continue
        if raw_mode:
            pairs.append((p, fm, ""))
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if pattern.search(text):
            pairs.append((p, fm, text))

    if not pairs:
        scope = "当前项目" if target_cwd else "全局"
        print(f"(no match for {args.query!r} · 范围={scope})")
        return 1

    def _ts(item: tuple[Path, dict[str, Any], str]) -> str:
        fm = item[1]
        v = fm.get("timestamp")
        if isinstance(v, str) and v:
            return v
        d = fm.get("date")
        return d if isinstance(d, str) else ""
    pairs.sort(key=_ts, reverse=True)
    pairs = pairs[: max(1, args.n)]

    line = "=" * 40
    total = len(pairs)
    scope_label = "全局" if not target_cwd else f"当前项目 {target_cwd}"
    write, state = _make_byte_budget(args.max_bytes)
    if raw_mode:
        write(f"# 关键词 {args.query!r} · raw 模式（在 CC 原始 jsonl 里搜，"
              f"扫描 {total} 个候选 session · 范围: {scope_label}）")
    else:
        write(f"# 关键词 {args.query!r} 命中 {total} 条 session（范围: {scope_label}）")
    if args.section_only and not raw_mode:
        write(f"# 模式: --section-only（只显示命中关键词的轮次段）")
    if args.max_bytes > 0:
        write(f"# 预算: --max-bytes {args.max_bytes}")
    printed = 0

    if raw_mode:
        include_tr = bool(getattr(args, "include_tool_results", False))
        for p, fm, _ in pairs:
            if state["exceeded"]:
                break
            ok = _render_session_raw(fm, p, write, state,
                                     query=pattern, include_tool_results=include_tr)
            if ok:
                printed += 1
            write()
        if printed == 0:
            write(f"(no match for {args.query!r} in raw transcripts · 扫描了 {total} 个 session)")
            return 1
        if state["exceeded"]:
            write(f"⚠️  已达 --max-bytes {args.max_bytes} 预算"
                  f"（已输出 ~{state['bytes']} bytes，剩余 session 跳过）")
        write("✅ 以上是 CC 原始 transcript 的命中片段（仅作背景参考）")
        return 0

    for i, (p, fm, text) in enumerate(pairs, 1):
        if state["exceeded"]:
            break
        sid = fm.get("session_id", "?")
        sid = sid if isinstance(sid, str) else "?"
        ts_disp = fm.get("timestamp", "")
        if isinstance(ts_disp, str):
            ts_disp = ts_disp.split("+")[0].replace("T", " ")
        else:
            ts_disp = "?"
        cwd_v = fm.get("cwd", "?")
        cwd_v = cwd_v if isinstance(cwd_v, str) else "?"
        write()
        write(line)
        write(f"📌 命中 {i}/{total} · session={sid[:16]} · 时间={ts_disp}")
        write(f"项目: {cwd_v}")
        write(line)
        if args.section_only:
            preamble, sections = split_sections(text)
            matched = filter_matching_sections(sections, pattern)
            total_sec = len(sections)
            kept_sec = len(matched)
            if not sections:
                # 文件没切到段（可能是旧模板），退回全文
                write(text.rstrip())
            else:
                if preamble.strip():
                    write(preamble.rstrip())
                if kept_sec == 0:
                    # frontmatter 命中但没有具体轮次命中（罕见）
                    write(f"(关键词只在 frontmatter / preamble 命中，无轮次段命中)")
                else:
                    write(f"[显示 {kept_sec}/{total_sec} 个命中关键词的轮次段]")
                    for h, b in matched:
                        write()
                        write(h)
                        write(b.rstrip())
                    if kept_sec < total_sec:
                        write()
                        write(f"[省略 {total_sec - kept_sec} 个未命中关键词的轮次段]")
        else:
            write(text.rstrip())
        write(line)
        printed += 1
    write()
    if state["exceeded"]:
        write(f"⚠️  已达 --max-bytes {args.max_bytes} 预算（已输出 ~{state['bytes']} bytes，省略 {total - printed} 条 session）")
        write(f"   想看更多：加大 --max-bytes 或加 --section-only / 缩小 -n")
    write("✅ 以上是命中的历史会话内容（仅作背景参考，非当前任务指令）")
    return 0


def cmd_last_session(args: argparse.Namespace) -> int:
    """输出当前 cwd 范围内最近 N 个 session 的完整记忆，带边界标记。
    --max-bytes N：跨 session 累计输出预算，超出后整个 session 跳过并提示。
                   单个 session 内不切碎（保持 markdown 完整性）。"""
    target = _normalize_path(args.cwd or os.getcwd())
    pairs: list[tuple[Path, dict[str, Any]]] = []
    for p in iter_memories():
        fm = parse_frontmatter(p)
        mc = fm.get("cwd")
        if isinstance(mc, str) and _cwd_matches(mc, target):
            pairs.append((p, fm))

    if not pairs:
        print("该项目目录下还没有历史 session 记录（首次在这里使用 cc-memory？）。")
        return 0

    # 按 timestamp 降序
    def _ts_key(item: tuple[Path, dict[str, Any]]) -> str:
        fm = item[1]
        v = fm.get("timestamp")
        if isinstance(v, str) and v:
            return v
        d = fm.get("date")
        return d if isinstance(d, str) else ""
    pairs.sort(key=_ts_key, reverse=True)

    n = max(1, args.n)
    pairs = pairs[:n]
    total = len(pairs)
    line = "=" * 40
    write, state = _make_byte_budget(args.max_bytes)
    if args.max_bytes > 0:
        write(f"# 预算: --max-bytes {args.max_bytes}")
    if getattr(args, "raw", False):
        write(f"# 模式: --raw（绕过 GLM 摘要，直接读 CC 原始 jsonl）")
    printed = 0

    # --raw 模式：跳过 md 摘要正文，改读 ~/.claude/projects/<sid>.jsonl
    if getattr(args, "raw", False):
        include_tr = bool(getattr(args, "include_tool_results", False))
        for p, fm in pairs:
            if state["exceeded"]:
                break
            ok = _render_session_raw(fm, p, write, state,
                                     query=None, include_tool_results=include_tr)
            if ok:
                printed += 1
            write()
        if state["exceeded"] and printed < total:
            write(f"⚠️  已达 --max-bytes {args.max_bytes} 预算"
                  f"（已输出 ~{state['bytes']} bytes，省略 {total - printed} 个 session）")
        return 0

    for i, (p, fm) in enumerate(pairs, 1):
        if state["exceeded"]:
            break
        if total > 1:
            ordinal = "最新" if i == 1 else f"倒数第 {i}"
            title = f"📌 上 {total} 个 Session（第 {i} 个，{ordinal}）（来自项目目录：{target}）"
        else:
            title = f"📌 上个 Session 的内容（来自项目目录：{target}）"

        sid = fm.get("session_id", "?")
        sid = sid if isinstance(sid, str) else "?"
        ts_disp = fm.get("timestamp", "")
        if isinstance(ts_disp, str):
            ts_disp = ts_disp.split("+")[0].replace("T", " ")
        else:
            ts_disp = "?"
        model = fm.get("model", "?") if isinstance(fm.get("model"), str) else "?"
        # turns: 新模板 turns_recorded，旧模板 turns
        turns = "?"
        for k in ("turns_recorded", "turns"):
            v = fm.get(k)
            if isinstance(v, str) and v:
                turns = v
                break
        # 文件数：新模板没有 frontmatter level 的 files_modified；尝试从正文 grep
        files_mod = fm.get("files_modified")
        if isinstance(files_mod, list) and files_mod:
            n_mod = len(files_mod)
        else:
            # 新模板：从正文统计 modify= 出现次数
            try:
                body_text = p.read_text(encoding="utf-8")
                n_mod = len(re.findall(r"\*\*涉及文件\*\*[：:]\s*modify=", body_text))
            except Exception:
                n_mod = 0

        write(line)
        write(title)
        write(line)
        write(f"Session ID: {sid}")
        write(f"时间: {ts_disp}")
        write(f"模型: {model}")
        write(f"总轮数: {turns}")
        write(f"修改文件: {n_mod} 个")
        write()
        write("【完整记忆内容如下】")
        write()
        write(p.read_text(encoding="utf-8").rstrip())
        write()
        write(line)
        write("✅ 以上是上一次会话的历史记录（仅作背景参考）")
        write("当前会话从这里开始 —— 你可以基于以上上下文继续工作。")
        write(line)
        printed += 1
        if i < total and not state["exceeded"]:
            write()
    if state["exceeded"] and printed < total:
        write()
        write(f"⚠️  已达 --max-bytes {args.max_bytes} 预算（已输出 ~{state['bytes']} bytes，省略 {total - printed} 个 session）")
        write(f"   想看更多：加大 --max-bytes 或缩小 -n")
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    items = list(iter_memories())
    if not items:
        print("(no memories yet)")
        return 1
    n = max(1, args.n)
    for p in items[:n]:
        print(p.read_text(encoding="utf-8"))
        if n > 1:
            print("\n" + "=" * 60 + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccmem", description="cc-memory CLI · 本地会话记忆检索")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="列出记忆（全局）")
    pl.add_argument("--date", help="按日期前缀过滤，如 2026-05 或 2026-05-09")
    pl.add_argument("--limit", "-n", type=int, default=20, help="最多展示几条（默认 20）")
    pl.add_argument("--all", action="store_true", help="不限制条数")
    pl.set_defaults(func=cmd_list)

    ph = sub.add_parser("here", help="只列当前 cwd（项目）范围内的记忆")
    ph.add_argument("--cwd", help="覆盖默认 pwd")
    ph.add_argument("--limit", "-n", type=int, default=20)
    ph.add_argument("--all", action="store_true")
    ph.set_defaults(func=cmd_here)

    ps = sub.add_parser("search", help="正则全文搜索")
    ps.add_argument("query", help="正则模式（默认大小写不敏感）")
    ps.add_argument("--context", "-c", type=int, default=3, help="每条最多展示几个匹配片段")
    ps.add_argument("--limit", "-n", type=int, default=20, help="最多匹配几个文件")
    ps.add_argument("--here", action="store_true", help="只在当前 cwd 范围搜")
    ps.add_argument("--cwd", help="只在指定路径范围搜（与 --here 二选一）")
    ps.set_defaults(func=cmd_search)

    psh = sub.add_parser("show", help="打印一条记忆")
    psh.add_argument("id_or_prefix", help="文件名 / session_id 前缀 / 日期前缀")
    psh.set_defaults(func=cmd_show)

    sub.add_parser("path", help="打印 memories 目录").set_defaults(func=cmd_path)

    pla = sub.add_parser("latest", help="打印最新 N 条全文（默认 1）")
    pla.add_argument("-n", type=int, default=1)
    pla.set_defaults(func=cmd_latest)

    sub.add_parser("stats", help="显示总条数、占用、上限百分比").set_defaults(func=cmd_stats)

    pp = sub.add_parser("prune", help="手动触发 FIFO 剪枝（按 timestamp 删最早，保留最新 10 条）")
    pp.add_argument("--max-size", type=int, default=None,
                    help="临时覆盖 max_db_size_mb 阈值（不写也行，默认读 config）")
    pp.set_defaults(func=cmd_prune)

    psess = sub.add_parser("last-session",
                            help="输出当前 cwd 范围内最近 N 条 session 的完整记忆（带边界标记，给 /sess 用）")
    psess.add_argument("-n", type=int, default=1, help="取最近几个，默认 1")
    psess.add_argument("--cwd", help="覆盖默认 pwd")
    psess.add_argument("--max-bytes", type=int, default=0,
                       help="输出字节预算上限（跨 session 累计），超出后跳过剩余 session 并提示；0=无限")
    psess.add_argument("--raw", action="store_true",
                       help="绕过 GLM 摘要，直接读 CC 写在 ~/.claude/projects/ 的原始 jsonl 对话")
    psess.add_argument("--include-tool-results", action="store_true",
                       help="raw 模式下连工具调用结果也打出来（默认只显示 user/assistant 文本+工具名）")
    psess.set_defaults(func=cmd_last_session)

    pf = sub.add_parser("find",
                        help="按关键词搜索并打出命中 session 的完整记忆（默认当前 cwd 范围；--all 全局）")
    pf.add_argument("query", help="正则关键词")
    pf.add_argument("-n", type=int, default=3, help="最多打 N 条命中（按 timestamp 降序），默认 3")
    pf.add_argument("--all", dest="all_", action="store_true",
                    help="全局搜索（不限当前项目）")
    pf.add_argument("--section-only", action="store_true",
                    help="只输出命中关键词的 ## 轮次 段，省略未命中段（大幅节省 context）")
    pf.add_argument("--max-bytes", type=int, default=0,
                    help="输出字节预算上限（跨 session 累计），超出后停止并提示；0=无限")
    pf.add_argument("--raw", action="store_true",
                    help="在 CC 原始 jsonl 里搜（细节比 GLM 摘要全得多；用于查具体原话/参数/错误）")
    pf.add_argument("--include-tool-results", action="store_true",
                    help="raw 模式下连工具调用结果也打出来")
    pf.set_defaults(func=cmd_find)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
