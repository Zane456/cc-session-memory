# cc-memory · 设计方案

一个为 Claude Code 设计的轻量级会话记忆系统。借鉴 [claude-mem](https://github.com/thedotmack/claude-mem) 的 hook-driven 思路，三处关键差异：

1. **用 Stop hook 增量记录**：每轮 Claude 回完话就总结这一轮、append 到当 session 的同一个 md 文件里。**不依赖 SessionEnd**，CC 怎么挂最多丢"还没回完的最后一轮"
2. **总结引擎走 GLM API**（z.ai 智谱清言）而非 Claude，省 token
3. **不在 SessionStart 注入上下文**，需要时手动 `/recall` 或 `/sess`

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Claude Code session                      │
│                                                              │
│  用户提问 → Claude 回答完一轮 ──►  Stop 事件触发              │
│                                       │                      │
└───────────────────────────────────────┼──────────────────────┘
                                        │ stdin: {session_id, transcript_path,
                                        │   stop_hook_active, last_assistant_message, ...}
                                        ▼
                       ┌────────────────────────────────┐
                       │  hooks/session_end.sh          │
                       │  · 把 stdin 写到 tmp 文件       │
                       │  · nohup setsid 拆离 python     │
                       │  · 立即 exit 0                 │ ← CC 不阻塞
                       └────────────┬───────────────────┘
                                    │ detach（后台异步）
                                    ▼
                       ┌────────────────────────────────┐
                       │  hooks/summarize.py            │
                       │  ① 检查 stop_hook_active；若 true 退│
                       │  ② 检查 assistant 内容长度；太短退  │
                       │  ③ 从 transcript 抽"最后一对" U/A  │
                       │  ④ POST GLM 总结这一轮（≤ 100 字） │
                       │  ⑤ flock + append 到 session md   │
                       │     更新 frontmatter 计数 / 时间    │
                       │  ⑥ 写完做容量检查（FIFO 剪枝）      │
                       └────────────┬───────────────────┘
                                    │
                                    ▼
            memories/YYYY-MM-DD-<sid8>.md（同 session 累加）

      ┌─── 用户主动检索 ───────────────────────────────┐
      │  cli/ccmem.py  ── grep 搜索 markdown           │
      │  · /recall <kw>   cwd 范围搜索                  │
      │  · /sess          加载本项目最近一次 session    │
      └─────────────────────────────────────────────────┘
```

**关键不变量**：同一个 CC session（同一个 session_id）→ 同一个 md 文件。每轮触发时增量 append。

---

## 2. 文件结构

```
/path/to/cc-project-memory/        # repo 根目录（举例）
├── .claude/
│   ├── settings.json              # Claude Code 项目级 hook 配置
│   └── commands/
│       └── recall.md              # 可选 slash 命令（手动检索）
├── memory_system/
│   ├── hooks/
│   │   ├── session_end.sh         # bash 拆离器
│   │   └── summarize.py           # python worker（调 GLM、写 md）
│   ├── cli/
│   │   └── ccmem.py               # 检索 CLI（search / list / show）
│   ├── config/
│   │   └── config.example.json    # 配置模板
│   └── README.md
├── memories/                       # 总结产物
│   ├── .gitkeep
│   └── 2026-05-09-abc12345.md
├── .gitignore                      # 忽略 logs / 真 config / 等
└── README.md                       # 顶层说明
```

**配置文件位置**：`~/.config/cc-memory/config.json`（用户私有，不进 repo）。  
**日志位置**：`~/.config/cc-memory/logs/run-<timestamp>.log`。

---

## 3. Hook 选型：Stop（每轮 append，而非 SessionEnd 一次性总结）

| Hook | 触发时机 | 选择 | 理由 |
|---|---|---|---|
| **Stop** | Claude 每轮回完话 | ✅ | 增量、可靠；CC 怎么挂都最多丢"未完成那轮" |
| SessionEnd | 整个 session 终止时 | ❌ | 关窗/Cmd+Q/kill -9 都不一定触发；一次性总结整段 token 更贵 |

**Stop hook 的关键 payload 字段**（按 [官方文档](https://docs.claude.com/en/docs/claude-code/hooks#stop)）：
- `session_id`、`transcript_path`、`cwd`、`hook_event_name`（基础字段）
- `last_assistant_message` —— Claude 这一轮的最终文本（直接用，省去再解析 transcript）
- **`stop_hook_active`** —— hook 自身引发的再次 Stop 时为 `true`，必须检测并跳过，**否则会死循环**（worker 触发 Stop 触发 worker…）

文档原话：

> The `stop_hook_active` field is `true` when Claude Code is already continuing as a result of a stop hook. **Check this value or process the transcript to prevent Claude Code from running indefinitely.**

我们的实现里第一行检查就是：

```python
if event.get("stop_hook_active"):
    log.info("stop_hook_active=true, skip (prevent loop)")
    return 0
```

---

## 4. 关键脚本要点

### `session_end.sh`（bash 拆离器，~15 行）

```bash
#!/usr/bin/env bash
set -euo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 把 stdin（hook payload JSON）写到临时文件
TMP="$(mktemp -t ccmem-payload.XXXXXX)"
cat > "$TMP"

LOG_DIR="$HOME/.config/cc-memory/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"

# 完全 detach：setsid 创建新会话，nohup 忽略 HUP，重定向 std fd，& 后台
nohup setsid python3 "$HOOK_DIR/summarize.py" "$TMP" \
    >> "$LOG" 2>&1 < /dev/null &
disown

exit 0   # 立即返回，主进程不阻塞
```

为什么用 setsid + nohup + disown：三重保险，确保父进程退出后子进程继续跑。  
为什么写临时文件而非管道：管道在父进程退出后会断，文件传参更稳。

### `summarize.py`（核心 worker · per-turn append）

```
1. sys.argv[1] = payload tmp 文件路径，读完即删
2. 检查 stop_hook_active；为 true 直接 return 0（防死循环）
3. 检查 last_assistant_message 长度；< min_assistant_chars (默认 50) 跳过，不调 GLM
4. 从 transcript 倒推"最近一对" (user prompt, assistant 文本, 工具调用文件)
   · 跳过 tool_result-only 的 user 消息（那不是新提问）
   · 收集本轮 tool_use 里 file_path（Edit/Write/MultiEdit/NotebookEdit → modify；Read → read）
5. 拼 prompt：「请用 2-3 句话总结这一轮：用户:.../结果:.../关键词:...」整体 ≤ 100 字
6. POST /chat/completions（max_tokens=200）
7. 用 fcntl.flock(LOCK_EX) 打开 memories/YYYY-MM-DD-<sid8>.md：
   · 文件不存在 → 写完整 frontmatter + 第 1 段轮次
   · 已存在 → 解析 frontmatter，turns_recorded += 1，total_tokens 累加，
              last_update 刷成现在；body 末尾 append 新轮次段
8. 写完做容量检查（FIFO 剪枝），最新 10 条永不删
9. 全程 try/except，失败 → 把错误以 ## ⚠️ GLM 失败 段 append 到当 session 文件，永不抛回 CC
```

### `ccmem.py`（CLI 检索）

```bash
# 列表 / 搜索
ccmem list                       # 最近 20 条（全局）
ccmem list --date 2026-05        # 按日期过滤
ccmem here                       # 仅当前项目目录范围
ccmem search "GLM"               # 正则搜索（全局）
ccmem search "GLM" --here        # 正则搜索 + 限当前 cwd
ccmem search "GLM" --cwd /x      # 正则搜索 + 限指定目录
ccmem recall [关键词] [--all]      # /recall slash 派发入口

# 单条
ccmem show <id-prefix>           # 打印一条
ccmem latest -n 1                # 最新 N 条全文
ccmem last-session [-n 1]        # 当前 cwd 最新 N 条全文，带边界标记（/sess 用）

# 元信息 / 维护
ccmem path                       # memories 目录
ccmem stats                      # 总条数 / 占用 / 上限百分比
ccmem prune [--max-size MB]      # 手动 FIFO 剪枝
```

实现方式：纯标准库，遍历 markdown，正则匹配，无需索引。文件不大时 grep 完全够用。

---

## 5. 存储格式（一个 session = 一个 md 文件，多个轮次段）

**理念**：每轮极简记录（用户意图 / 结果 / 关键词），同 session 累积成一份 md。`max_tokens=200` 每轮强约束。

frontmatter 是 session 级元信息（每轮 append 时重写）；body 是按时间顺序排列的多个"轮次"段。

```markdown
---
session_id: sess-aaaaaaaa
date: 2026-05-09
start_time: 03:57:16          # 首次创建时刻（不变）
last_update: 03:57:18         # 每轮 append 时刷新
timestamp: 2026-05-09T03:57:18+00:00
cwd: /path/to/cc-project-memory
model: glm-5-turbo
turns_recorded: 3             # 累计写入的轮次数（每 append +1）
total_tokens: 1230            # 累计 GLM 消耗（每轮加）
---

# 会话记录 · 2026-05-09（CC project memory）

## 轮次 1 · 03:57:16
**用户**：想做 帮我给 utils.py 加 logging 模块的初始化
**结果**：完成第 1 步，给出了答案
**涉及文件**：modify=utils.py
**关键词**：logging, utils.py, init

## 轮次 2 · 03:57:17
**用户**：现在给 utils.py 加单元测试
**结果**：创建了 test_utils.py，2 个用例
**涉及文件**：modify=test_utils.py
**关键词**：pytest, test_utils.py

## 轮次 3 · 03:57:18
**用户**：跑一下测试看通不通
**结果**：测试全部通过，2 用例 OK
**涉及文件**：（无）
**关键词**：bash, pytest, pass
```

**关键约束**：同 session_id 总是写到同一个文件（不会因为时间跨日产生第 2 个文件 —— 因为文件名按 session 创建那天命名，session_id 又稳定）。每轮 append 时持有 `fcntl.LOCK_EX` 排它锁防并发。

frontmatter 让 grep / 脚本检索都很方便，正文极短让人眼一眼看完，关键词段为 grep 显式预留命中目标。

---

## 5.5 Token 经济学（Stop-per-turn vs SessionEnd-once）

新架构每轮调一次 GLM，总 token 用量随轮数线性增长。粗略对比：

| 场景 | SessionEnd 一次性（旧） | Stop per-turn（新） | 差异 |
|---|---|---|---|
| 5 轮会话 | 1 × (~3K in + 250 out) ≈ 3.25K | 5 × (~500 in + 150 out) ≈ 3.25K | 平 |
| 10 轮会话 | 1 × (~6K in + 250 out) ≈ 6.25K | 10 × (~500 in + 150 out) ≈ 6.5K | 略涨 |
| 20 轮会话 | 1 × (~12K in + 250 out) ≈ 12.25K | 20 × (~500 in + 150 out) ≈ 13K | 略涨 |

**output token 涨幅最大**（z.ai 计费 output 比 input 贵）：5 轮 750 vs 250，约 **3×**。

**换来的好处**：
- CC 怎么挂都最多丢"未完成的最后一轮"（关窗、Cmd+Q、kill -9 都不影响已写记录）
- 每轮单独可读、可 grep；上下文链路更清晰
- 一个 session md 增量增长，可在跑过一半中途用 `ccmem latest` 看进展

如果对 token 敏感，可以提高 `min_assistant_chars`（比如 100）让短回答跳过总结，或者把 `max_tokens` 进一步压到 150。

---

## 5.6 容量保护：FIFO 剪枝

为避免 `memories/` 长期累积无上限：

- 配置字段 `max_db_size_mb`（默认 200，0 = 关闭）
- 每次 `summarize.py` 写入新记忆**之后**自动调 `enforce_size_cap()`
- 剪枝策略：
  - 按 frontmatter 的 `timestamp` 升序（最早在前）
  - 删到 `max_db_size_mb × 0.9` 以下（避免下次写入立即又触发）
  - **始终保留最新 10 条**——哪怕这 10 条已经超上限，也只打 warning，不删
  - frontmatter 损坏时回退到文件名 `YYYY-MM-DD` 排序
- 用户可随时跑 `ccmem stats` 看占用，`ccmem prune [--max-size MB]` 手动整理

---

## 6. 安全 & 隐私

- API key **不入 repo**：存 `~/.config/cc-memory/config.json`，权限 `chmod 600`
- `.gitignore` 忽略 `logs/`、`*.log`、`config.json`、`memories/*.md`（根据用户偏好可改）
- transcript 内容只发给 GLM 一次，不持久化原文

---

## 7. 端到端测试计划（mock GLM）

1. 准备假 transcript JSONL（3 轮对话）
2. 准备 mock GLM 服务器（python http.server 监听本地端口，返回固定 JSON）
3. config.json 指向 mock
4. `echo '{...payload...}' | bash session_end.sh`
5. 立即 `ps` 确认 python 子进程在跑、bash 已退出
6. 等待 1 秒，检查 `memories/` 是否生成 .md
7. `python ccmem.py search ...` 能搜到
8. **关键验证**：bash 退出 ≤ 100ms，python 在 detach 状态运行

---

## 8. 部署现状（2026-05-09）

| 项 | 选定值 |
|---|---|
| **GLM Endpoint** | `https://api.z.ai/api/anthropic/v1/messages`（z.ai Anthropic Messages 兼容；该账号在 OpenAI-compat 端点没资源包，会 1113） |
| **GLM 模型** | `glm-5-turbo`（次选 `glm-5.1` / `glm-5`） |
| **触发 hook** | Stop（每轮 append；hook 配在 `~/.claude/settings.json` 全局，所有项目通吃） |
| **memories 是否进 git** | `memories/` 是独立 git 仓 → `Zane456/cc-project-memory` (private)；launchd `com.cc-memory.daily-push` 每天 22:00 push |
| **slash 命令** | `~/.claude/commands/{sess,recall}.md` 全局生效 |
| **失败隔离** | GLM 调用失败的错误段写到 `~/.config/cc-memory/failures/`，不进 memories/，不会被推到 GitHub |
| **配置文件** | `~/.config/cc-memory/config.json` (chmod 600) |
