# memory_system/

cc-session-memory 的全部代码与配置都在这里。三个模块：

```
hooks/        Claude Code SessionEnd hook 的实现
cli/          手动检索的 Python CLI
bin/          一次性安装脚本
config/       配置模板（真配置在 ~/.config/cc-session-memory/config.json）
```

## hooks/

### `session_end.sh`
- 由 Claude Code 在每次 SessionEnd 调起
- 把 stdin（hook payload JSON）落到 `mktemp` 文件
- `nohup setsid python3 ... & disown` 把 worker 拆离
- 立即 `exit 0`，bash 部分实测 < 50ms

### `summarize.py`
- 读 payload tmp 文件 → 解析 → 删除
- 读 `transcript_path` 指向的 JSONL，抽出 user/assistant 文本（丢工具调用，省 token）
- 加载 `~/.config/cc-session-memory/config.json`（或 `$CC_MEMORY_CONFIG`）
- POST z.ai chat-completions（OpenAI 兼容格式）
- 把总结写成 `memories/YYYY-MM-DD-<sid8>.md`，含 frontmatter
- 失败永远不向上抛，写日志即可

## cli/ccmem.py

```bash
ccmem list                    # 最近 20 条
ccmem list --date 2026-05    # 按月份过滤
ccmem search "关键词"          # 正则全文搜索
ccmem show <id-prefix>        # 打印一条
ccmem latest -n 1             # 打印最新 N 条全文（适合 /sess 加载上下文）
ccmem path                    # 打印 memories 目录
```

## bin/setup.sh

一次性安装脚本：

```bash
./setup.sh --key <z.ai_api_key>
# 或
./setup.sh                        # 交互式
# 或
ZAI_API_KEY=xxx ./setup.sh
```

会做三件事：
1. 创建 `~/.config/cc-session-memory/config.json`，把 api_key 填进去，`chmod 600`
2. 创建 `~/.config/cc-session-memory/logs/`
3. 给 hooks/cli 设可执行位

## config/config.example.json

模板。所有字段：

| 字段 | 默认 | 说明 |
|---|---|---|
| `api_key` | `YOUR_...` | z.ai API key |
| `endpoint` | `https://api.z.ai/api/paas/v4/chat/completions` | OpenAI 兼容 chat completions |
| `model` | `glm-5-turbo` | 可选 `glm-5.1`/`glm-5`/`glm-4.6`/`glm-4.5-flash` 等 |
| `thinking_enabled` | `false` | glm-5/5.1/5-turbo 默认强制思考；总结任务关掉省 token。设 `true` 可换更高质量总结 |
| `temperature` | `0.3` | 总结任务，低温度更稳 |
| `max_tokens` | `400` | 单次总结上限（轻量模板） |
| `max_db_size_mb` | `200` | memories/ 总容量上限；超出按 timestamp FIFO 剪枝到 90%；最新 10 条永不删；设 `0` 关闭 |
| `request_timeout_seconds` | `60` | HTTP 超时 |
| `memories_dir` | （空 = 用本仓库的 memories/） | 自定义存储目录 |
| `transcript_truncate_chars` | `60000` | 长会话截断 |
| `language` | `zh` | 当前 prompt 写死中文，预留字段 |

## 环境变量

| 变量 | 作用 |
|---|---|
| `CC_MEMORY_HOME` | 改默认 `~/.config/cc-session-memory` |
| `CC_MEMORY_CONFIG` | 直接指定 config.json 路径（测试用） |
| `CC_MEMORY_DIR` | 改默认 memories 目录（CLI 用） |
| `CC_MEMORY_PYTHON` | 改 python 解释器（默认 python3） |
