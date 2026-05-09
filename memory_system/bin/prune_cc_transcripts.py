#!/usr/bin/env python3
"""Prune ~/.claude/projects/ transcripts to stay under a size cap.

Claude Code 自己写在 ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl 的
完整对话日志。这个仓库里的 cc-memory 只管 memories/ 摘要，不管这些原始 transcript。
本脚本独立做容量保护：超过 cap 时按 mtime 升序删最旧的 .jsonl，删到 cap*0.9 以下；
保护最近 24h 内有写入的文件（视为活跃 session）。删完后清空残留空目录。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CAP_GB = 3.0
DEFAULT_KEEP_RECENT_HOURS = 24
TRIM_RATIO = 0.9  # 删到 cap × 0.9 以下，避免下次写入立即又触发


def _human(n_bytes: int) -> str:
    f = float(n_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if f < 1024 or unit == "T":
            return f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}P"


def collect_jsonls(root: Path) -> list[tuple[Path, float, int]]:
    """Return [(path, mtime, size)] for every *.jsonl under root."""
    out: list[tuple[Path, float, int]] = []
    for p in root.rglob("*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((p, st.st_mtime, st.st_size))
    return out


def cleanup_empty_dirs(root: Path) -> int:
    """Bottom-up remove empty dirs under root; return count removed."""
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == str(root):
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                removed += 1
        except OSError:
            pass
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cap-gb", type=float, default=DEFAULT_CAP_GB,
                    help=f"size cap in GB (default {DEFAULT_CAP_GB})")
    ap.add_argument("--keep-recent-hours", type=int,
                    default=DEFAULT_KEEP_RECENT_HOURS,
                    help=f"never delete files mtime within last N hours "
                         f"(default {DEFAULT_KEEP_RECENT_HOURS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be deleted, do not actually rm")
    ap.add_argument("--root", type=str, default=str(PROJECTS_DIR),
                    help="override scan root (default ~/.claude/projects)")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"[skip] {root} 不存在或不是目录", file=sys.stderr)
        return 0

    cap_bytes = int(args.cap_gb * 1024 * 1024 * 1024)
    trim_target = int(cap_bytes * TRIM_RATIO)
    cutoff_mtime = time.time() - args.keep_recent_hours * 3600

    files = collect_jsonls(root)
    total = sum(s for _, _, s in files)
    print(f"[scan] root={root}")
    print(f"[scan] {len(files)} jsonl files, total {_human(total)}")
    print(f"[cap]  {_human(cap_bytes)} (trim to {_human(trim_target)} when over)")

    if total <= cap_bytes:
        print(f"[ok]   under cap, nothing to do")
        return 0

    # 升序：oldest first
    files.sort(key=lambda x: x[1])

    to_delete: list[tuple[Path, float, int]] = []
    skipped_recent = 0
    running = total
    for path, mtime, size in files:
        if running <= trim_target:
            break
        if mtime > cutoff_mtime:
            skipped_recent += 1
            continue
        to_delete.append((path, mtime, size))
        running -= size

    if running > trim_target:
        print(f"[warn] 仅靠删非活跃文件无法降到 {_human(trim_target)}；"
              f"剩余 {_human(running)}（其余 {skipped_recent} 个文件在保护窗口内）")

    if not to_delete:
        print(f"[noop] 超额 {_human(total - cap_bytes)}，但保护窗口内没有可删文件")
        return 0

    freed = sum(s for _, _, s in to_delete)
    label = "[DRY-RUN] would delete" if args.dry_run else "[delete]"
    print(f"{label} {len(to_delete)} files, free {_human(freed)}")

    if args.dry_run:
        for p, mt, sz in to_delete[:10]:
            age_d = (time.time() - mt) / 86400
            print(f"  {_human(sz):>8}  {age_d:5.1f}d  {p}")
        if len(to_delete) > 10:
            print(f"  ... and {len(to_delete) - 10} more")
        return 0

    deleted = 0
    for p, _, _ in to_delete:
        try:
            p.unlink()
            deleted += 1
        except OSError as e:
            print(f"[err] {p}: {e}", file=sys.stderr)

    empty_removed = cleanup_empty_dirs(root)
    final = sum(s for _, _, s in collect_jsonls(root))
    print(f"[done] removed {deleted} files, {empty_removed} empty dirs, "
          f"now {_human(final)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
