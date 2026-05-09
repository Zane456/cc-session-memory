#!/usr/bin/env bash
# cc-memory · SessionEnd hook 拆离器
# 任务：把 hook payload (stdin JSON) 转给 python worker，并立即返回，绝不阻塞 Claude Code。
#
# 拆离策略（macOS / Linux 通用）：
#   1) nohup     —— 忽略 SIGHUP，父进程退出后子进程不会被杀
#   2) setsid    —— Linux 上创建独立进程组；macOS 默认无此命令，自动跳过
#   3) </dev/null + & + disown —— 切断 stdin、后台运行、从 shell job table 移除

set -euo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER="$HOOK_DIR/summarize.py"

# 配置 / 日志目录
CONF_DIR="${CC_MEMORY_HOME:-$HOME/.config/cc-memory}"
LOG_DIR="$CONF_DIR/logs"
mkdir -p "$LOG_DIR"

LOG="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S)-$$.log"

# 把 stdin 落盘到临时 payload 文件，python 启动后自己删
TMP_PAYLOAD="$(mktemp -t ccmem-payload.XXXXXX)"
if ! cat > "$TMP_PAYLOAD"; then
    echo "[$(date -Iseconds)] failed to read stdin" >> "$LOG"
    rm -f "$TMP_PAYLOAD"
    exit 0   # 永远以 0 退出，不影响 Claude Code
fi

# 检查 payload 是否为空（手动测试时可能没 stdin）
if [ ! -s "$TMP_PAYLOAD" ]; then
    echo "[$(date -Iseconds)] empty payload, skip" >> "$LOG"
    rm -f "$TMP_PAYLOAD"
    exit 0
fi

PYTHON_BIN="${CC_MEMORY_PYTHON:-python3}"

# 完全 detach 启动 worker（stdin 切到 /dev/null 防止悬挂）
# setsid 在 Linux util-linux 里有，macOS 默认没有；能用就用，没有就只用 nohup
if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$PYTHON_BIN" "$WORKER" "$TMP_PAYLOAD" \
        >> "$LOG" 2>&1 < /dev/null &
else
    nohup "$PYTHON_BIN" "$WORKER" "$TMP_PAYLOAD" \
        >> "$LOG" 2>&1 < /dev/null &
fi
disown || true

# 立即返回。Claude Code 关心的是这个 hook 的退出码与执行时间。
exit 0
