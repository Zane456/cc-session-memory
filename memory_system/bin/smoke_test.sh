#!/usr/bin/env bash
# cc-session-memory · 真实 LLM API 烟雾测试
# 验证：
#   1) ~/.config/cc-session-memory/config.json 可读
#   2) config 里的 endpoint 可达 + api_key 有效（无论 OpenAI / Anthropic / 兼容端点）
#   3) hook 拆离 < 200ms
#   4) python worker 真的能调你配置的 LLM 写出 markdown
#
# 默认会用真实 memories_dir → 留下 1 条 smoke-xxx 记忆。
# 加 --isolated：所有产出（config 副本 + memories 副本）写到 mktemp -d 的临时目录，跑完即清理。
#
# 用法：
#   ./smoke_test.sh                  # 跑真实 config，会污染 memories/
#   ./smoke_test.sh --isolated       # 临时目录里跑，不动你真 memories

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_DIR="${CC_SESSION_MEMORY_HOME:-$HOME/.config/cc-session-memory}"
CONF="$CONF_DIR/config.json"

ISOLATED=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --isolated|-i) ISOLATED=1; shift ;;
        -h|--help)
            sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 64 ;;
    esac
done

if [ ! -f "$CONF" ]; then
    echo "❌ $CONF 不存在 —— 先跑 ./setup.sh" >&2
    exit 1
fi

# --isolated：把 config 复制到 tmpdir，把 memories_dir 改成 tmpdir 子目录
if [ "$ISOLATED" -eq 1 ]; then
    TMP="$(mktemp -d -t ccmem-smoke.XXXXXX)"
    trap 'rm -rf "$TMP"' EXIT
    ISO_CONF="$TMP/config.json"
    ISO_MEM="$TMP/memories"
    mkdir -p "$ISO_MEM" "$TMP/logs"
    python3 - "$CONF" "$ISO_CONF" "$ISO_MEM" <<'PY'
import json, sys
src, dst, mem = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(src))
cfg["memories_dir"] = mem
json.dump(cfg, open(dst, "w"), ensure_ascii=False, indent=2)
PY
    chmod 600 "$ISO_CONF"
    export CC_SESSION_MEMORY_HOME="$TMP"
    export CC_SESSION_MEMORY_CONFIG="$ISO_CONF"
    CONF="$ISO_CONF"
    echo "─── 0) --isolated 模式：临时目录 = $TMP ───"
fi

echo "─── 1) 读 config ───"
python3 -c "
import json
c = json.load(open('$CONF'))
print(f'  endpoint     = {c[\"endpoint\"]}')
print(f'  model        = {c[\"model\"]}')
print(f'  key tail     = ...{c[\"api_key\"][-6:]}')
print(f'  memories_dir = {c.get(\"memories_dir\")}')
"

echo
echo "─── 2) curl 一次最小 chat 请求 ───"
python3 - "$CONF" <<'PY'
import json, sys, urllib.request, urllib.error, time
cfg = json.load(open(sys.argv[1]))
body = json.dumps({
    "model": cfg["model"],
    "messages": [{"role":"user","content":"回复一个字：好"}],
    "max_tokens": 32,
    "temperature": 0,
    "stream": False,
    "thinking": {"type": "enabled" if cfg.get("thinking_enabled") else "disabled"},
}).encode()
req = urllib.request.Request(cfg["endpoint"], data=body, headers={
    "Authorization": f"Bearer {cfg['api_key']}",
    "Content-Type": "application/json",
}, method="POST")
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
except urllib.error.HTTPError as e:
    print(f"  ❌ HTTP {e.code}: {e.read().decode()[:300]}")
    sys.exit(2)
print(f"  ✓ HTTP 200 in {time.time()-t0:.2f}s")
print(f"  ✓ model = {data.get('model')}")
print(f"  ✓ reply = {data['choices'][0]['message']['content']!r}")
print(f"  ✓ usage = {data.get('usage')}")
PY

echo
echo "─── 3) 模拟 Stop hook payload，验证拆离时间 ───"
TRANSCRIPT="$(mktemp -t ccmem-smoke-tr.XXXXXX.jsonl)"
cat > "$TRANSCRIPT" <<'JSONL'
{"type":"user","message":{"role":"user","content":"smoke test：解释一下什么是 cc-session-memory 的 Stop hook。"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"cc-session-memory 的 Stop hook 在 Claude 每轮回完话时触发，会异步用配置的 LLM 总结这一轮并 append 到当 session 的 markdown 文件，全程不阻塞 Claude Code 的退出。这是真实端到端调用测试。"}]}}
JSONL

LAST_ASSIST="cc-session-memory 的 Stop hook 在 Claude 每轮回完话时触发，会异步用配置的 LLM 总结这一轮并 append 到当 session 的 markdown 文件，全程不阻塞 Claude Code 的退出。这是真实端到端调用测试。"
PAYLOAD=$(python3 -c "
import json,sys,time
print(json.dumps({
    'session_id': f'smoke-{int(time.time())}',
    'transcript_path': '$TRANSCRIPT',
    'cwd': '$PWD',
    'hook_event_name': 'Stop',
    'stop_hook_active': False,
    'last_assistant_message': '''$LAST_ASSIST'''
}, ensure_ascii=False))
")

T0=$(python3 -c "import time;print(int(time.time()*1000))")
echo "$PAYLOAD" | bash "$ROOT/hooks/session_end.sh"
T1=$(python3 -c "import time;print(int(time.time()*1000))")
ELAPSED=$((T1 - T0))
echo "  bash 拆离耗时 = ${ELAPSED}ms"
if [ "$ELAPSED" -gt 200 ]; then
    echo "  ⚠️  > 200ms，可能阻塞了"
else
    echo "  ✓ 非阻塞"
fi

echo
echo "─── 4) 等 worker 调 LLM 完成（最长 30s） ───"
MEM_DIR=$(python3 -c "import json; print(json.load(open('$CONF')).get('memories_dir'))")
echo "  memories_dir = $MEM_DIR"

SID=$(echo "$PAYLOAD" | python3 -c "import json,sys;print(json.load(sys.stdin)['session_id'])")
SHORT=$(echo "$SID" | tr -dc 'a-zA-Z0-9' | cut -c1-8)
TARGET_GLOB="$MEM_DIR/$(date +%Y-%m-%d)-${SHORT}*.md"

for i in $(seq 1 60); do
    if ls $TARGET_GLOB 2>/dev/null | head -1 > /dev/null; then
        FILE=$(ls $TARGET_GLOB | head -1)
        echo "  ✓ 写入 $FILE"
        echo
        echo "─── 5) 文件内容（前 30 行） ───"
        head -30 "$FILE"
        rm -f "$TRANSCRIPT"
        echo
        echo "✅ smoke test 通过"
        if [ "$ISOLATED" -eq 1 ]; then
            echo "（--isolated：临时目录 $TMP 即将清理，不影响你真 memories）"
        fi
        exit 0
    fi
    sleep 0.5
done

rm -f "$TRANSCRIPT"
LOG_DIR="${CC_SESSION_MEMORY_HOME:-$HOME/.config/cc-session-memory}/logs"
echo "  ❌ 30s 内未生成 memory 文件" >&2
echo "  看 $LOG_DIR/worker.log 排查" >&2
tail -30 "$LOG_DIR/worker.log" 2>/dev/null || true
exit 3
