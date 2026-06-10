#!/usr/bin/env python3
"""cc-memory · Stop hook worker（每轮 append 架构）

每次 Claude Code 完成一轮回答 → Stop hook 触发 → 这个 worker 跑一次：
  1. 检查 stop_hook_active；为 true 直接退（防止 hook 自身再触发死循环）
  2. 检查 last_assistant_message：太短（< 50 chars）→ 跳过，不调 LLM
  3. 从 transcript JSONL 抽出"最后一对" (user prompt, assistant final text, tool 文件清单)
  4. 调你配置的 LLM（OpenAI Chat Completions 或 Anthropic Messages 协议）总结这一轮
  5. 文件操作（带 fcntl 排它锁）：
        · session 第一次：创建 memories/YYYY-MM-DD-<sid>.md，写 frontmatter + 第 1 段轮次
        · 后续：append 新轮次段，frontmatter 里 turns_recorded / last_update / total_tokens 重写

LLM 协议由 config.json 的 `protocol` 字段决定（"openai" | "anthropic"）；不设的话从
endpoint URL 自动嗅探（含 /messages 或 /anthropic/ → anthropic，否则 openai）。
只用 Python 标准库（urllib + json + fcntl）。全程 try/except，失败写日志，绝不抛回 CC。
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import skill_usage  # memory_system/skill_usage.py，skill 调用流水（与 ccmem 共用）
except Exception:
    skill_usage = None  # 统计是附属功能，缺了不影响总结主流程

# ────────────────────────────────────────────────────────────────────────────
# 路径与默认值
# ────────────────────────────────────────────────────────────────────────────

CC_MEMORY_HOME = Path(os.environ.get("CC_MEMORY_HOME", str(Path.home() / ".config" / "cc-memory")))
DEFAULT_CONFIG_PATH = CC_MEMORY_HOME / "config.json"
LOG_DIR = CC_MEMORY_HOME / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "worker.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("cc-memory")


# ────────────────────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────────────────────

DEFAULTS = {
    # Default points at OpenAI Chat Completions schema (most universal).
    # Switch to your provider by overriding endpoint / model / protocol in config.
    # See INSTALL.md "Provider matrix" for examples (OpenAI / Anthropic / DeepSeek / Ollama / OpenRouter / Z.AI / ...).
    # NOTE: 'protocol' is intentionally NOT set in DEFAULTS — it gets auto-sniffed
    # from the endpoint URL so old configs without an explicit 'protocol' field
    # keep working (e.g. /api/anthropic/v1/messages → anthropic).
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "temperature": 0.3,
    "max_tokens": 600,           # 每轮 200-300 字的"读了就懂"总结，预算 600 token
    "request_timeout_seconds": 60,
    "max_db_size_mb": 200,
    "min_assistant_chars": 50,   # 这一轮 assistant 内容低于这个长度就跳过 LLM
    "user_text_truncate_chars": 4000,    # 单轮 user 输入截断
    "assistant_text_truncate_chars": 4000,
    "language": "zh",
}

MIN_KEEP_NEWEST = 10
PRUNE_TARGET_RATIO = 0.9


def load_config() -> dict[str, Any]:
    cfg_path = Path(os.environ.get("CC_MEMORY_CONFIG", str(DEFAULT_CONFIG_PATH)))
    if not cfg_path.exists():
        raise FileNotFoundError(f"config not found at {cfg_path}; run setup.sh first")
    with cfg_path.open(encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("api_key") or cfg["api_key"].startswith("YOUR_"):
        raise ValueError(f"api_key missing or placeholder in {cfg_path}")
    return {**DEFAULTS, **cfg}


# ────────────────────────────────────────────────────────────────────────────
# 单轮抽取：从 transcript 末尾找"最后一对" (user, assistant, tool_files)
# ────────────────────────────────────────────────────────────────────────────

MODIFY_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read"}
PATH_KEYS = ("file_path", "notebook_path", "path")


def _extract_text(content: Any) -> str:
    """抽出可读文本，丢工具调用占位用 [tool_use: Name]。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type")
                if t == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif t == "tool_use":
                    parts.append(f"[tool_use: {item.get('name', '?')}]")
                elif t == "tool_result":
                    parts.append("[tool_result]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def _is_tool_result_only(content: Any) -> bool:
    """判断 user 消息是不是 100% 工具结果（不算"用户的新提问"）。"""
    if not isinstance(content, list):
        return False
    if not content:
        return False
    return all(isinstance(c, dict) and c.get("type") == "tool_result" for c in content)


def extract_last_turn(
    transcript_path: str,
    last_assistant_from_payload: str = "",
    user_truncate: int = 4000,
    asst_truncate: int = 4000,
) -> dict[str, Any]:
    """从 transcript 末尾找"最近一轮" = 最近一个非 tool_result 的 user msg + 它后面所有 assistant msgs/tool_uses。

    返回 {user_text, assistant_text, modified, read}。
    `last_assistant_from_payload` 优先作为 assistant_text；若为空回退到 transcript 末尾抽出的 assistant 文本。
    """
    if not transcript_path:
        return {
            "user_text": "",
            "assistant_text": last_assistant_from_payload or "",
            "modified": [],
            "read": [],
        }
    p = Path(transcript_path)
    if not p.exists() or p.is_dir():
        log.warning("transcript not found: %s", transcript_path)
        return {
            "user_text": "",
            "assistant_text": last_assistant_from_payload or "",
            "modified": [],
            "read": [],
        }

    lines: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not lines:
        return {"user_text": "", "assistant_text": last_assistant_from_payload, "modified": [], "read": []}

    # 倒着找最近的非-tool_result user 消息
    user_idx = None
    for i in range(len(lines) - 1, -1, -1):
        obj = lines[i]
        msg = obj.get("message") or obj
        role = msg.get("role") or obj.get("type")
        content = msg.get("content") if isinstance(msg, dict) else None
        if role == "user" and not _is_tool_result_only(content):
            user_idx = i
            break

    if user_idx is None:
        return {
            "user_text": "",
            "assistant_text": last_assistant_from_payload,
            "modified": [],
            "read": [],
        }

    user_msg = lines[user_idx].get("message") or lines[user_idx]
    user_content = user_msg.get("content") if isinstance(user_msg, dict) else None
    user_text = _extract_text(user_content)
    if len(user_text) > user_truncate:
        user_text = user_text[: user_truncate] + "…（截断）"

    # 收集本轮的 assistant 文本和工具调用
    asst_texts: list[str] = []
    modified: list[str] = []
    read: list[str] = []
    seen_mod: set[str] = set()
    seen_read: set[str] = set()

    for obj in lines[user_idx + 1:]:
        msg = obj.get("message") or obj
        role = msg.get("role") or obj.get("type")
        content = msg.get("content") if isinstance(msg, dict) else None
        if role == "assistant":
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    t = item.get("type")
                    if t == "text" and isinstance(item.get("text"), str):
                        asst_texts.append(item["text"])
                    elif t == "tool_use":
                        name = item.get("name")
                        inp = item.get("input")
                        if not isinstance(inp, dict):
                            continue
                        fp = next(
                            (inp[k] for k in PATH_KEYS
                             if isinstance(inp.get(k), str) and inp.get(k)),
                            None,
                        )
                        if not fp:
                            continue
                        if name in MODIFY_TOOLS and fp not in seen_mod:
                            seen_mod.add(fp); modified.append(fp)
                        elif name in READ_TOOLS and fp not in seen_read:
                            seen_read.add(fp); read.append(fp)
            elif isinstance(content, str):
                asst_texts.append(content)

    asst_text = last_assistant_from_payload.strip() or "\n".join(asst_texts).strip()
    if len(asst_text) > asst_truncate:
        asst_text = asst_text[: asst_truncate] + "…（截断）"

    read = [f for f in read if f not in seen_mod]
    skill_events = skill_usage.extract_events(lines[user_idx:]) if skill_usage else []
    return {"user_text": user_text, "assistant_text": asst_text, "modified": modified,
            "read": read, "skill_events": skill_events}


# ────────────────────────────────────────────────────────────────────────────
# LLM 调用：单轮总结
# ────────────────────────────────────────────────────────────────────────────

PROMPT_TURN_ZH = """请用中文详细总结下面这一轮 Claude Code 对话。目标是：让别的大模型只看你写的总结、不看原文，就基本能知道"出了什么问题、是什么情况、做了什么决定、有没有失败"。

严格按这个格式输出（不带 frontmatter，不带 ## 标题，每行就是字面这几行）：

用户: <用户的核心意图 + 必要的上下文（之前在做什么、哪个前置失败了、为什么这么问）。≤ 80 字>
结果: <Claude 给的最终结论 / 产出 / 诊断。要写具体：发现的问题是什么、采用了哪个方案、哪些文件被改了哪部分、关键数值/参数、踩过哪些坑、哪些尝试失败了被推翻。≤ 220 字>
关键词: <5-10 个关键词，逗号分隔。包含具体名字（文件名/函数名/符号/错误码/工具名/数值/MPN）+ 类目词（bug/refactor/config/...）>

要求：
- 全部中文（关键词、文件名、API 名、错误信息保留原样）
- 整体 ≤ 320 字
- 保留具体名字、文件名、API 名、错误信息、关键数值、决策依据
- 失败/绕弯要写出来（"试过 X 但 Y 失败，最终走 Z"），不要只写最终方案
- 不要写"在这次会话中..."、"用户希望..."这类废话；直接陈述事实

---
用户消息：
{user_text}

---
助手最终回复：
{assistant_text}
"""


SYSTEM_PROMPT = "你是单轮对话总结助手。输出短、准、可 grep。"


def _detect_protocol(cfg: dict[str, Any]) -> str:
    """config.protocol 没设时从 endpoint 嗅探。
    含 /messages 或 /anthropic/ → anthropic；否则 openai。"""
    p = cfg.get("protocol")
    if p in ("openai", "anthropic"):
        return p
    ep = (cfg.get("endpoint") or "").lower()
    if ep.endswith("/messages") or "/anthropic/" in ep or "/anthropic" in ep:
        return "anthropic"
    return "openai"


def _build_anthropic_request(cfg: dict[str, Any], user_msg: str) -> tuple[bytes, dict[str, str]]:
    payload = {
        "model": cfg["model"],
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}],
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 600),
        "stream": False,
    }
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers


def _parse_anthropic_response(data: dict[str, Any]) -> str:
    if "content" not in data or not data["content"]:
        return ""
    first = data["content"][0]
    if isinstance(first, dict):
        return (first.get("text") or "").strip()
    return ""


def _build_openai_request(cfg: dict[str, Any], user_msg: str) -> tuple[bytes, dict[str, str]]:
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 600),
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers


def _parse_openai_response(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    msg = choices[0].get("message") or {}
    if isinstance(msg, dict):
        return (msg.get("content") or "").strip()
    return ""


def call_llm_for_turn(
    cfg: dict[str, Any],
    user_text: str,
    assistant_text: str,
) -> dict[str, Any]:
    """调用配置的 LLM 总结单轮，返回 {summary, model, usage, elapsed, protocol}。
    支持两种协议：
      - openai: POST {endpoint} with Authorization: Bearer，标准 Chat Completions 格式
      - anthropic: POST {endpoint} with x-api-key + anthropic-version，Messages 格式
    协议从 cfg["protocol"] 取，没设的话从 endpoint URL 自动嗅探。"""
    user_msg = PROMPT_TURN_ZH.format(user_text=user_text, assistant_text=assistant_text)

    protocol = _detect_protocol(cfg)
    if protocol == "anthropic":
        body, headers = _build_anthropic_request(cfg, user_msg)
        parser = _parse_anthropic_response
    else:
        body, headers = _build_openai_request(cfg, user_msg)
        parser = _parse_openai_response

    req = urllib.request.Request(cfg["endpoint"], data=body, headers=headers, method="POST")
    timeout = cfg.get("request_timeout_seconds", 60)
    log.info("calling LLM: protocol=%s model=%s", protocol, cfg["model"])
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {e.code} ({protocol}): {err_body[:500]}") from e
    elapsed = time.time() - started

    data = json.loads(raw)
    summary = parser(data)
    if not summary:
        raise RuntimeError(f"unexpected {protocol} response: {raw[:500]}")
    usage = data.get("usage") or {}
    log.info("LLM ok in %.2fs · protocol=%s · usage=%s", elapsed, protocol, usage)
    return {
        "summary": summary,
        "model": data.get("model", cfg["model"]),
        "usage": usage,
        "elapsed": elapsed,
        "protocol": protocol,
    }


# ────────────────────────────────────────────────────────────────────────────
# 解析 LLM 直出的三行 → 结构化字段
# ────────────────────────────────────────────────────────────────────────────

def parse_turn_summary(s: str) -> dict[str, str]:
    """把 LLM 输出的"用户:.../结果:.../关键词:..." 三行解析成 dict。
    LLM 偶尔会乱加 markdown 强调，宽松匹配。"""
    out = {"user": "", "result": "", "keywords": ""}
    for line in s.splitlines():
        # 去掉前导的 *、- 等
        line = re.sub(r"^[\*\-\s]+", "", line).strip()
        for key, alias in (("user", "用户"), ("result", "结果"), ("keywords", "关键词")):
            if line.startswith(alias + ":") or line.startswith(alias + "：") or \
               line.startswith("**" + alias + "**:") or line.startswith("**" + alias + "**：") or \
               line.lower().startswith(alias.lower() + ":"):
                # 取冒号后内容
                _, _, val = line.partition(":")
                if not val:
                    _, _, val = line.partition("：")
                out[key] = val.strip().strip("*").strip()
                break
    return out


# ────────────────────────────────────────────────────────────────────────────
# 文件 IO：创建 + append（带 fcntl 锁）
# ────────────────────────────────────────────────────────────────────────────

def _short_sid(session_id: str, length: int = 8) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", session_id)[:length] or "nosess"


def _build_frontmatter(
    session_id: str,
    cwd: str,
    start_iso: str,
    last_iso: str,
    model: str,
    turns_recorded: int,
    total_tokens: int,
) -> str:
    return (
        "---\n"
        f"session_id: {session_id}\n"
        f"date: {start_iso[:10]}\n"
        f"start_time: {start_iso[11:19]}\n"
        f"last_update: {last_iso[11:19]}\n"
        f"timestamp: {last_iso}\n"
        f"cwd: {cwd}\n"
        f"model: {model}\n"
        f"turns_recorded: {turns_recorded}\n"
        f"total_tokens: {total_tokens}\n"
        "---\n\n"
    )


def _build_turn_block(
    n: int,
    time_str: str,
    parsed: dict[str, str],
    modified: list[str],
    read: list[str],
) -> str:
    files_line = ""
    mods = [Path(f).name for f in modified]
    reads = [Path(f).name for f in read]
    if mods or reads:
        parts = []
        if mods:
            parts.append(f"modify={', '.join(mods)}")
        if reads:
            parts.append(f"read={', '.join(reads)}")
        files_line = f"**涉及文件**：{' · '.join(parts)}\n"
    else:
        files_line = "**涉及文件**：（无）\n"

    user = parsed.get("user") or "(未识别)"
    result = parsed.get("result") or "(未识别)"
    keywords = parsed.get("keywords") or ""

    return (
        f"## 轮次 {n} · {time_str}\n"
        f"**用户**：{user}\n"
        f"**结果**：{result}\n"
        f"{files_line}"
        f"**关键词**：{keywords}\n"
        "\n"
    )


def _parse_existing_frontmatter(text: str) -> dict[str, Any] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    fm: dict[str, Any] = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    fm["__body_start"] = end + 4  # 跳过 "\n---\n"
    return fm


def append_turn_to_session_file(
    target: Path,
    cfg: dict[str, Any],
    event: dict[str, Any],
    parsed: dict[str, str],
    tool_files: dict[str, list[str]],
    usage: dict[str, Any],
) -> dict[str, Any]:
    """打开（或创建）session md，加锁，append 一段轮次，更新 frontmatter。

    返回 {turn_n, total_tokens, target}。
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    session_id = str(event.get("session_id") or "unknown")
    cwd = event.get("cwd") or os.getcwd()
    model = cfg["model"]

    now = datetime.now().astimezone()
    now_iso = now.isoformat(timespec="seconds")
    time_str = now.strftime("%H:%M:%S")
    turn_tokens = int(
        usage.get("total_tokens", 0)
        or (int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0))
        or 0
    )

    # 锁拆到 sidecar 文件上，避免 open(target, w+b) 在 flock 之前就截断的竞态。
    # 写入用 temp + os.replace 原子化，git push 中途读到的永远是完整版本（没有半成品）。
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_fd = open(lock_path, "a+b")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        is_new = not existing

        if is_new:
            new_frontmatter = _build_frontmatter(
                session_id=session_id,
                cwd=cwd,
                start_iso=now_iso,
                last_iso=now_iso,
                model=model,
                turns_recorded=1,
                total_tokens=turn_tokens,
            )
            title = f"# 会话记录 · {now.strftime('%Y-%m-%d')}（{Path(cwd).name}）\n\n"
            block = _build_turn_block(1, time_str, parsed, tool_files["modified"], tool_files["read"])
            new_content = new_frontmatter + title + block
            new_turn_n = 1
            new_total = turn_tokens
        else:
            fm = _parse_existing_frontmatter(existing)
            if fm is None:
                log.warning("existing file has no frontmatter, treating as new content")
                prev_turns = 0
                prev_tokens = 0
                body = existing
            else:
                try:
                    prev_turns = int(fm.get("turns_recorded", "0") or 0)
                except ValueError:
                    prev_turns = 0
                try:
                    prev_tokens = int(fm.get("total_tokens", "0") or 0)
                except ValueError:
                    prev_tokens = 0
                body = existing[fm["__body_start"]:]

            new_turn_n = prev_turns + 1
            new_total = prev_tokens + turn_tokens

            new_frontmatter = _build_frontmatter(
                session_id=session_id,
                cwd=cwd,
                start_iso=(fm.get("date", now.strftime("%Y-%m-%d")) + "T" + fm.get("start_time", time_str) + "+00:00") if fm else now_iso,
                last_iso=now_iso,
                model=model,
                turns_recorded=new_turn_n,
                total_tokens=new_total,
            )
            body = body.lstrip("\n")
            if not body.endswith("\n"):
                body += "\n"
            new_block = _build_turn_block(new_turn_n, time_str, parsed, tool_files["modified"], tool_files["read"])
            new_content = new_frontmatter + body + new_block

        # 原子写入：temp → fsync → os.replace
        tmp_path = target.with_suffix(target.suffix + ".tmp")
        with open(tmp_path, "wb") as tmp_fd:
            tmp_fd.write(new_content.encode("utf-8"))
            tmp_fd.flush()
            os.fsync(tmp_fd.fileno())
        os.replace(tmp_path, target)

        if is_new:
            log.info("created new session md: %s · turn 1", target)
            return {"turn_n": 1, "total_tokens": turn_tokens, "target": target, "created": True}
        log.info("appended turn %d to %s · cumulative_tokens=%d", new_turn_n, target, new_total)
        return {"turn_n": new_turn_n, "total_tokens": new_total, "target": target, "created": False}
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


# ────────────────────────────────────────────────────────────────────────────
# 容量保护
# ────────────────────────────────────────────────────────────────────────────

def _read_memory_timestamp(p: Path) -> str:
    try:
        with p.open(encoding="utf-8") as f:
            head = f.read(2000)
        if head.startswith("---"):
            end = head.find("\n---", 3)
            if end > 0:
                for line in head[3:end].splitlines():
                    if line.startswith("timestamp:"):
                        ts = line.partition(":")[2].strip()
                        if ts:
                            return ts
                for line in head[3:end].splitlines():
                    if line.startswith("date:"):
                        d = line.partition(":")[2].strip()
                        if d:
                            return f"{d}T00:00:00"
    except Exception:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
    if m:
        return f"{m.group(1)}T00:00:00"
    return ""


def enforce_size_cap(memories_dir: Path, max_mb: int, log_func: Any = None) -> dict[str, Any]:
    if log_func is None:
        log_func = lambda msg: None
    if max_mb <= 0:
        return {"pruned": 0, "freed_bytes": 0, "before_bytes": 0, "after_bytes": 0,
                "oldest_pruned": "", "kept": 0, "max_mb": max_mb}
    max_bytes = int(max_mb * 1024 * 1024)
    target_bytes = int(max_bytes * PRUNE_TARGET_RATIO)
    items = [p for p in memories_dir.glob("*.md") if p.is_file()]
    if not items:
        return {"pruned": 0, "freed_bytes": 0, "before_bytes": 0, "after_bytes": 0,
                "oldest_pruned": "", "kept": 0, "max_mb": max_mb}
    triples: list[tuple[Path, int, str]] = []
    for p in items:
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        triples.append((p, sz, _read_memory_timestamp(p)))
    total = sum(t[1] for t in triples)
    if total <= max_bytes:
        return {"pruned": 0, "freed_bytes": 0, "before_bytes": total, "after_bytes": total,
                "oldest_pruned": "", "kept": len(triples), "max_mb": max_mb}
    triples.sort(key=lambda t: t[2])
    protected_names = {t[0].name for t in sorted(triples, key=lambda t: t[2], reverse=True)[:MIN_KEEP_NEWEST]}
    pruned = 0; freed = 0; oldest_pruned_ts = ""; current = total
    for p, sz, ts in triples:
        if current <= target_bytes:
            break
        if p.name in protected_names:
            continue
        try:
            p.unlink()
        except OSError as e:
            log_func(f"prune: failed to delete {p}: {e}")
            continue
        if pruned == 0:
            oldest_pruned_ts = ts
        current -= sz; freed += sz; pruned += 1
    if pruned > 0:
        log_func("pruned %d memories (oldest: %s), freed %.1f MB, current %.1f MB / %d MB cap" %
                 (pruned, (oldest_pruned_ts or "?")[:10], freed/1024/1024, current/1024/1024, max_mb))
    elif current > max_bytes:
        log_func("size cap hit but %d newest memories alone are %.1f MB (> %d MB cap); keeping them" %
                 (MIN_KEEP_NEWEST, current/1024/1024, max_mb))
    return {"pruned": pruned, "freed_bytes": freed, "before_bytes": total, "after_bytes": current,
            "oldest_pruned": oldest_pruned_ts[:10] if oldest_pruned_ts else "",
            "kept": len(triples) - pruned, "max_mb": max_mb}


# ────────────────────────────────────────────────────────────────────────────
# 失败兜底
# ────────────────────────────────────────────────────────────────────────────

def _memories_dir(cfg: dict[str, Any]) -> Path:
    return Path(cfg.get("memories_dir") or (Path(__file__).resolve().parents[2] / "memories"))


def _write_failure_note(cfg: dict[str, Any], event: dict[str, Any], err: str) -> None:
    """LLM 调用失败写到 ~/.config/cc-memory/failures/，不污染 memories/（不会被推到 GitHub）。"""
    try:
        fail_dir = CC_MEMORY_HOME / "failures"
        fail_dir.mkdir(parents=True, exist_ok=True)
        sid = _short_sid(str(event.get("session_id") or "unknown"))
        date = datetime.now().strftime("%Y-%m-%d")
        target = fail_dir / f"{date}-{sid}.log"
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
        note = f"[{now_iso}] session={event.get('session_id')} cwd={event.get('cwd')} error={err[:500]}\n"
        with open(target, "ab") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(note.encode("utf-8"))
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        log.error("failure note write failed: %s", e)


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────

def main(payload_file: str) -> int:
    payload_path = Path(payload_file)
    try:
        event = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("cannot parse payload %s: %s", payload_file, e)
        return 1
    finally:
        try:
            payload_path.unlink()
        except OSError:
            pass

    # 防止 stop hook 自身引发的再次 Stop 触发死循环
    if event.get("stop_hook_active"):
        log.info("stop_hook_active=true, skip (prevent loop)")
        return 0

    log.info("event: session_id=%s hook=%s", event.get("session_id"), event.get("hook_event_name"))

    try:
        cfg = load_config()
    except Exception as e:
        log.error("config load failed: %s", e)
        return 2

    last_assistant = (event.get("last_assistant_message") or "").strip()
    min_chars = cfg.get("min_assistant_chars", 50)

    # 节流：assistant 这一轮内容太短就跳过（工具中间步骤、空响应等）
    if len(last_assistant) < min_chars and not event.get("transcript_path"):
        log.info("turn skipped (assistant content too short, no transcript)")
        return 0

    transcript_path = event.get("transcript_path") or ""
    turn = extract_last_turn(
        transcript_path,
        last_assistant_from_payload=last_assistant,
        user_truncate=cfg.get("user_text_truncate_chars", 4000),
        asst_truncate=cfg.get("assistant_text_truncate_chars", 4000),
    )

    # skill 调用流水：在节流之前落盘（/clear 这类短回复轮次也要计数），失败不影响总结
    if skill_usage and turn.get("skill_events"):
        try:
            for ev in turn["skill_events"]:
                ev["session_id"] = ev["session_id"] or str(event.get("session_id") or "")
                ev["cwd"] = ev["cwd"] or str(event.get("cwd") or "")
            n = skill_usage.append_events(
                skill_usage.usage_path(_memories_dir(cfg)), turn["skill_events"])
            if n:
                log.info("skill usage recorded: %d event(s)", n)
        except Exception as e:
            log.warning("skill usage record failed: %s", e)

    # 二次节流：抽完仍然太短
    if len(turn["assistant_text"]) < min_chars:
        log.info("turn skipped (assistant content < %d chars after extract)", min_chars)
        return 0

    if not turn["user_text"].strip():
        log.info("no user prompt found in transcript, using placeholder")
        turn["user_text"] = "(用户原始提问未识别)"

    log.info(
        "turn extracted: user=%d chars, asst=%d chars, modified=%d, read=%d",
        len(turn["user_text"]), len(turn["assistant_text"]),
        len(turn["modified"]), len(turn["read"]),
    )

    try:
        result = call_llm_for_turn(cfg, turn["user_text"], turn["assistant_text"])
    except Exception as e:
        log.error("LLM call failed: %s\n%s", e, traceback.format_exc())
        try:
            _write_failure_note(cfg, event, str(e))
        except Exception as inner:
            log.error("failure note also failed: %s", inner)
        return 3

    parsed = parse_turn_summary(result["summary"])

    sid = _short_sid(str(event.get("session_id") or "unknown"))
    md_dir = _memories_dir(cfg)
    # 同 session 跨午夜时复用首次创建的文件，避免拆成两份。
    existing = sorted(md_dir.glob(f"*-{sid}.md"))
    if existing:
        target = existing[0]
    else:
        date = datetime.now().strftime("%Y-%m-%d")
        target = md_dir / f"{date}-{sid}.md"

    try:
        info = append_turn_to_session_file(
            target=target,
            cfg=cfg,
            event=event,
            parsed=parsed,
            tool_files={"modified": turn["modified"], "read": turn["read"]},
            usage=result.get("usage", {}),
        )
    except Exception as e:
        log.error("file append failed: %s\n%s", e, traceback.format_exc())
        return 4

    # 写完后做一次容量检查
    try:
        enforce_size_cap(_memories_dir(cfg), int(cfg.get("max_db_size_mb", 200)), log.warning)
    except Exception as e:
        log.warning("size cap enforcement failed: %s", e)

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: summarize.py <payload-json-file>", file=sys.stderr)
        sys.exit(64)
    sys.exit(main(sys.argv[1]))
