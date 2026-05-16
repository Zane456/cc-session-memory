[English](README.md) | 简体中文

<div align="center">

# cc-memory

<p align="center">
  <img src="docs/images/architecture.png" alt="cc-memory：Claude Code 逐轮会话记忆——后台自动写入，按需显式读取" width="720" />
</p>

> *「写入全自动，读取你说了算。就这么简单。」*

[![Language: Python](https://img.shields.io/badge/Language-Python-blue.svg)]()
[![Platform: Claude%20Code](https://img.shields.io/badge/Platform-Claude_Code-blueviolet.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)]()

<br>

**Claude Code 的逐轮会话记忆——后台自动写，你需要时才读。**

<br>

9 家 LLM · 1 个 hook · 10 ms 返回 · 零依赖 · markdown + grep

<br>

[看效果](#看效果) · [为什么这样设计](#为什么这样设计) · [安装](#安装) · [Provider 矩阵](#provider-矩阵) · [工作原理](#工作原理) · [核心数字](#核心数字)

</div>

---

## 看效果

你用完 Claude Code 关掉窗口。hook 已经悄悄跑完了——每一轮对话都有摘要。

```bash
$ python3 memory_system/cli/ccmem.py last-session

# Session: 2026-05-15-a3f8c · 项目: Voice-Brother · 轮次: 12
#
# [Turn 1] 用户要求用 1.5 秒定时器方案实现流式 ASR。
#   Agent 选了 GCD DispatchSourceTimer 而非 Combine，引用了 speech-swift API。
# [Turn 2] 尝试 MLXAudioStreamBuffer 封装。失败——MLX 数组不支持动态 append。
#   改用 ring-buffer 方案。
# [Turn 3] Ring buffer 跑通了。MLX cache 涨到 2 GB——加了 MLXMemoryGovernor，
#   cacheLimit 256 MB + 每次转写后 clearCache()。修复后 active 稳定在 1.8 GB。
# ...
```

第二天开新 session，只在需要时拉记忆：

```
/sess
# → 上一次会话摘要载入 context。接着上次聊。

/sess "MLX cache"
# → 所有提到 "MLX cache" 的会话——grepable，只搜当前项目。

/sess "上次那个报错原话是什么"
# → sess skill 检测到「原话/具体细节」触发词 → 自动切 --raw
# → 直接读 Claude Code 自己的完整 JSONL 原始记录，不走有损摘要
```

这不是向量数据库在做跨项目语义搜索。<br>
这是 **一个 session 一个 markdown，天生 grep 友好**，默认按当前项目隔离。

---

## 为什么这样设计

<p align="center">
  <img src="docs/images/philosophy.png" alt="cc-memory 设计理念：后台自动写入，显式按需读取" width="560" />
</p>

灵感来自 [claude-mem](https://github.com/thedotmack/claude-mem)——但做了两处反共识取舍：

| | claude-mem | cc-memory |
|---|---|---|
| **Hook 数量** | 5 个（SessionStart / UserPromptSubmit / PostToolUse / Stop / SessionEnd） | **1 个**（仅 Stop——逐轮 append） |
| **写入时机** | session 期间持续观察 | **每轮一次**——崩溃最多丢 1 轮 |
| **总结引擎** | Claude agent-sdk | **你自己挑 LLM**（9+ 家，见下面矩阵） |
| **SessionStart 自动注入** | 是 | **否**——手动 `/sess` |
| **存储** | SQLite + Chroma 向量库 | **markdown + grep** |

**取舍 1——不做跨项目记忆。**
"跨所有项目搜索"听起来很酷——但搜回来的多数是**伪相关**（关键词撞车、名字一样实质不同），反而稀释当前项目的判断。cc-memory 按 `cwd` 隔离；加 `--all` 才扩到全局，你不问它不搜。

**取舍 2——不在 SessionStart 自动注入。**
Context window 是稀缺资源。自动注入 = 每次开会话都交一笔"可能用不上的上下文"的税，稀释当前任务的信号。对话足够长时，这笔税逼你提前 compact，损失更重要的信息。cc-memory 把决定权留给你：`/sess` 接着聊，不需要就让记忆静静躺在磁盘上。

> *写入应该自动且廉价，读取应该显式且可控。*

---

## 安装

**推荐：让 Claude Code 帮你装**（~3 分钟）：

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
claude
```

把 [INSTALL.md](INSTALL.md) 里的安装提示粘进去——Claude Code 会自动跑 `setup.sh`、配置你的 LLM provider、端到端验证。

**或者手动装：**

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
./memory_system/bin/setup.sh --global --key <你的-LLM-api-key>
```

完整指南：[INSTALL.md](INSTALL.md)。

---

## Provider 矩阵

9 家 provider，2 种协议，**零绑死**：

| Provider | endpoint | model（示例） | protocol |
|---|---|---|---|
| **OpenAI** | `api.openai.com/v1/chat/completions` | gpt-4o-mini | openai |
| **Anthropic** | `api.anthropic.com/v1/messages` | claude-haiku-4-5-20251001 | anthropic |
| **DeepSeek** | `api.deepseek.com/v1/chat/completions` | deepseek-chat | openai |
| **OpenRouter** | `openrouter.ai/api/v1/chat/completions` | anthropic/claude-haiku-4-5 | openai |
| **Together** | `api.together.xyz/v1/chat/completions` | meta-llama/Llama-3.3-70B-Instruct | openai |
| **Groq** | `api.groq.com/openai/v1/chat/completions` | llama-3.3-70b-versatile | openai |
| **Ollama**（本地、免费） | `localhost:11434/v1/chat/completions` | qwen2.5:7b | openai |
| **vLLM**（本地） | `localhost:8000/v1/chat/completions` | *你部署的模型* | openai |
| **Z.AI GLM** | `api.z.ai/api/anthropic/v1/messages` | glm-5-turbo | anthropic |

`protocol` 不写也行——worker 从 URL 自动嗅探（`/messages` 或 `/anthropic/` → anthropic，否则 openai）。

装完想换 provider？在 Claude Code 里说 *"把我的 cc-memory 配置换成 deepseek"*——Claude 会帮你改 config。

---

## 工作原理

```mermaid
sequenceDiagram
    CC as Claude Code
    H as Stop Hook (bash)
    W as Python Worker
    LLM as 你的 LLM

    CC->>H: 一轮结束 → hook 触发
    H->>W: nohup 拆离 (~10ms)
    H-->>CC: exit 0（立即返回）
    W->>LLM: 总结这一轮
    LLM-->>W: ~300 字
    W->>W: flock(LOCK_EX) → append 到 session.md
```

**Claude Code 每结束一轮，4 件事自动发生：**

**1. Stop hook 触发** — `session_end.sh` 通过 tmpfile 收到当前轮的 transcript。
**2. 立即拆离** — `nohup setsid python3 ... & disown`。bash ~10 ms 就返回，CC 零感知。
**3. LLM 总结** — 你选的 provider 生成 ~300 字摘要，保留具体名字、失败尝试、决策理由。
**4. 追加到 markdown** — `flock(LOCK_EX)` 防并发写冲突。一个 session 一个文件，内含多轮段落。

**双层存储：**

| 层 | 位置 | 单轮大小 | 读取方式 |
|---|---|---|---|
| LLM 摘要（有损、快） | `memories/YYYY-MM-DD-<sid>.md` | ~300 字 | `ccmem find`、`/sess` |
| CC 原始 transcript（无损、大） | `~/.claude/projects/<sid>.jsonl` | 完整对话 + 工具 I/O | `ccmem --raw`；用户说"原话/细节"时自动触发 |

---

## 核心数字

| 指标 | 数值 |
|---|---|
| **Hook 数** | 1（仅 Stop）——最小表面积，最大可靠性 |
| **Hook 返回** | ~10 ms（异步拆离，CC 零感知） |
| **每轮摘要** | ~300 字（保留名字、失败、决策理由） |
| **LLM provider** | 9+（OpenAI · Anthropic · DeepSeek · OpenRouter · Together · Groq · Ollama · vLLM · Z.AI） |
| **依赖** | 0（纯 Python stdlib） |
| **存储** | markdown + grep（无向量库、无 SQLite） |
| **容量上限** | 200 MB，FIFO 剪枝，最新 10 条永不删 |
| **崩溃恢复** | 最多丢 1 轮（关窗 / Cmd+Q / 段错误都一样） |
| **CLI 子命令** | 10 个 via `ccmem.py` |
| **代码量** | ~800 行 Python + ~100 行 Bash |

---

## CLI 速查

```bash
ccmem last-session              # 当前项目最近一次会话（摘要）
ccmem last-session --raw        # 改读 CC 原始 jsonl
ccmem find "<关键词>"            # 在当前项目摘要里搜
ccmem find "<关键词>" --all      # 扩到全局
ccmem stats                     # 磁盘占用
ccmem prune                     # 摘要 FIFO 剪枝

# 把 ~/.claude/projects/ 卡到 3 GB：
python3 memory_system/bin/prune_cc_transcripts.py --dry-run
```

在 Claude Code 里：
- `/sess` ——加载当前项目最近一次会话
- `/sess <关键词>` ——按关键词搜摘要
- 「上次那个 bug 的原话是什么」——sess skill 检测到"原话/详情"触发词，自动切 `--raw`

---

## 目录结构

```
cc-project-memory/
├── INSTALL.md                            # 安装指引（推荐入口）
├── DESIGN.md                             # 完整设计方案
├── memory_system/
│   ├── hooks/
│   │   ├── session_end.sh                # bash 拆离器（~10 ms 返回）
│   │   └── summarize.py                  # python worker（调 LLM、写 md）
│   ├── cli/ccmem.py                      # 检索 CLI（10 个子命令）
│   ├── bin/
│   │   ├── setup.sh                      # 一次性安装脚本
│   │   └── prune_cc_transcripts.py       # 把 ~/.claude/projects 卡到 3 GB
│   └── config/config.example.json
├── skills/sess/SKILL.md                  # /sess 语言触发 skill 模板
├── memories/                             # LLM 摘要（gitignored）
└── docs/images/                          # 架构图 + 理念图
```

完整架构见 [DESIGN.md](DESIGN.md)。

---

<div align="center">

> *「写入全自动，读取你说了算。就这么简单。」*

<br>

**Zane456** — 电力电子研究者 & AI 工具链构建者

| 平台 | 链接 |
| :--- | :--- |
| 🌐 GitHub | [Zane456](https://github.com/Zane456) |
| 𝕏 X / Twitter | [@ZaneZaneZzZZ](https://x.com/ZaneZaneZzZZ) |
| 📕 小红书 | [Zz302179383](https://www.xiaohongshu.com/user/profile/Zz302179383) |
| ✉️ Email | zz302179383@gmail.com |

<br>

⭐ 如果这个工具帮到了你的 Claude Code 工作流，给个 star——让更多人看到。

<br><br>

MIT License © [Zane456](https://github.com/Zane456)

</div>
