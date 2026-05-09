# cc-memory

> 🌐 [English](./README.md) · **中文**

一套为 Claude Code 设计的轻量级**逐轮**会话记忆系统。Claude 每回完一轮，`Stop` hook 触发，后台 Python worker 调 **z.ai 的 GLM API** 总结这一轮（省 token），append 到当前 session 的 markdown 文件。下次开新 session **不自动注入历史**——你显式用 `/sess` 拉，或者随口说 "上次原话是怎么说的"，`sess` skill 会自动切到 `--raw` 模式读原文。

> 📦 **想直接装？** 看 [INSTALL.md](./INSTALL.md)——推荐"让 Claude Code 帮你装"路径，3 分钟完事。

![架构](./docs/images/architecture.png)

## 为什么这样设计

![理念](./docs/images/philosophy.png)

设计灵感来自 [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)，但有两处反共识取舍：

| 维度 | claude-mem | cc-memory |
|---|---|---|
| Hook 数量 | 5 个（SessionStart / UserPromptSubmit / PostToolUse / Stop / SessionEnd） | **1 个（仅 Stop，每轮 append）** |
| 写入时机 | session 期间持续观察 | **每轮一次**——CC 怎么挂都最多丢未完成那轮 |
| 总结引擎 | Claude agent-sdk | **z.ai GLM API**（便宜） |
| SessionStart 自动注入 | 是 | **否**——手动 `/sess` 或 `/recall` |
| 存储 | SQLite + Chroma 向量库 | **markdown 文件 + grep** |

### 1. 不做跨项目记忆 ❌

claude-mem 用向量库做"跨所有项目"的语义检索，听起来很酷。但**真正会用 CC 的人本身就会做好项目管理**——每个项目都有自己的 `CLAUDE.md`、自己的文档、自己的代码。跨项目搜回来的多数是**伪相关**信号（关键词撞车、概念名字一样实质不同），反而稀释当前项目的判断。

→ cc-memory 默认按 `cwd` 隔离记忆。`/sess`、`/recall` 优先在当前项目里找，加 `--all` 才扩到全局。**不预设"你需要跨项目"。**

### 2. 不在 SessionStart 自动注入历史 ❌

claude-mem 每次开新会话时，自动把上一次 session 的摘要塞进 context window。**但 context window 是稀缺资源**——不是每次开会话都需要上次的历史。自动注入意味着每次都默认付一笔"可能用不上的上下文"的税，稀释当前任务的信号；当对话足够长，这笔税会逼你提前 compact，反而损失更重要的信息。

更糟的是它**剥夺了用户的判断**：连"要不要带历史"都替你决定了。

→ cc-memory 把"加载历史"做成**显式动作**：写入是后台自动的（每轮 append），但读取由你主动触发——`/sess` 接着上次聊，`/sess <keyword>` 查具体话题。不需要就让记忆静静躺在磁盘上。

两个取舍合起来一句话：**写入应该自动且廉价，读取应该显式且可控**。

## 双层存储

cc-memory 写自己的 GLM 摘要；**Claude Code 自己另写一份完整原始 transcript**（`/resume`、`/continue` 这类功能依赖它）。cc-memory 的 `--raw` 模式就读这一份。

| 层 | 位置 | 格式 | 单轮大小 | 怎么读 |
|---|---|---|---|---|
| GLM 摘要（有损、快） | `<repo>/memories/YYYY-MM-DD-<sid>.md` | markdown + frontmatter | ~300 字 | `ccmem find / last-session`、`/sess` |
| CC 原始 transcript（无损、大） | `~/.claude/projects/<encoded-cwd>/<sid>.jsonl` | line-delimited JSON | 完整对话 + 工具 I/O | `ccmem ... --raw`；`/sess` 在用户说"原话/具体细节/原文"时自动走 `--raw` |

CC 的原始 transcript 会无限膨胀（CC 自己不删）。cc-memory 自带 `memory_system/bin/prune_cc_transcripts.py`，默认把 `~/.claude/projects/` 卡在 3 GB（可调），按 mtime 升序删最旧，保护最近 24 小时活跃的文件。

## 快速开始

完整步骤见 [INSTALL.md](./INSTALL.md)。简版：

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
./memory_system/bin/setup.sh --global --key <你的-z.ai-key>
```

CLI 速查：

```bash
python3 memory_system/cli/ccmem.py last-session              # 当前项目最近一次（摘要）
python3 memory_system/cli/ccmem.py last-session --raw        # 改读 CC 原始 jsonl
python3 memory_system/cli/ccmem.py find "<关键词>"            # 在当前项目摘要里搜
python3 memory_system/cli/ccmem.py find "<关键词>" --raw      # 直接搜原始 jsonl
python3 memory_system/cli/ccmem.py find "<关键词>" --all      # 扩到全局
python3 memory_system/cli/ccmem.py stats                     # 占用情况
python3 memory_system/cli/ccmem.py prune                     # 摘要 FIFO 剪枝

python3 memory_system/bin/prune_cc_transcripts.py --dry-run  # 把 ~/.claude/projects 卡到 3 GB
```

在 Claude Code 里：

- `/sess`——加载当前项目最近一次会话
- `/sess <关键词>`——按关键词搜摘要
- 「上次那个 bug 的原话是什么」——sess skill 检测到"原话/详情"等触发词，自动切 `--raw`

## 目录结构

```
.
├── INSTALL.md                            # 安装指引（推荐入口）
├── DESIGN.md                             # 完整设计方案
├── memory_system/
│   ├── hooks/
│   │   ├── session_end.sh                # bash 拆离器（~10 ms 返回）
│   │   └── summarize.py                  # python worker（调 GLM、写 md）
│   ├── cli/ccmem.py                      # 检索 CLI
│   ├── bin/
│   │   ├── setup.sh                      # 一次性安装脚本
│   │   └── prune_cc_transcripts.py       # 把 ~/.claude/projects 卡到 3 GB
│   └── config/config.example.json
├── skills/                               # ~/.claude/skills/ 镜像（模板）
│   ├── README.md                         # 安装/同步说明
│   └── sess/SKILL.md                     # /sess 语言触发 skill
├── memories/                             # GLM 摘要（gitignored）
└── docs/images/                          # 上面那两张图
```

## 关键设计决策

| 问题 | 选择 | 理由 |
|---|---|---|
| `Stop` vs `SessionEnd` hook | **Stop**（每轮 append） | 增量、可靠：关窗 / Cmd+Q / 崩溃都最多丢"未完成的最后一轮"。`SessionEnd` 反而不一定触发（见 DESIGN §3）|
| 死循环防护 | 检测 `stop_hook_active=true` 就 return | 避免 hook 自身引发的 Stop 再触发 worker 无限套娃（CC 文档明确警告）|
| 阻塞 vs 异步 | `nohup setsid python3 ... & disown` | bash 立即 exit 0（~10 ms），python 独立会话继续跑 |
| tmpfile vs stdin pipe | tmpfile | pipe 在父进程退出时会断，文件最稳 |
| 文件并发 | `fcntl.flock(LOCK_EX)` | 两个 Stop 几乎同时触发也不会相互覆盖 |
| 存储格式 | markdown + frontmatter（一 session 一文件，多轮次段） | grep 友好，人眼可读，无依赖 |
| 摘要长度 | ~300 字 / 轮（`max_tokens=600`） | 详尽到让别的模型只看摘要就能知道发生了什么、踩过哪些坑 |
| 容量上限 | `max_db_size_mb=200`，FIFO 剪枝到 90%，**最新 10 条永不删** | 防止长期累积爆盘 |
| 配置位置 | `~/.config/cc-memory/config.json`（chmod 600） | 用户私有，不进 repo |
| 失败处理 | GLM 失败 → 写到 `~/.config/cc-memory/failures/`，永不传回 CC | 永远 `exit 0` |

## 安全

- **API key 不入 git**：存在 `~/.config/cc-memory/config.json`（chmod 600）；`.gitignore` 也兜底忽略 `**/config.json`。
- **memories 默认不入 git**。要版本化的话，把 `memories/` 指到一个独立的 private repo（作者本人的做法），或删 `.gitignore` 里那行。
- **日志不含 key**，但建议定期清理 `~/.config/cc-memory/logs/`。

## 故障排查

```bash
# worker 日志
tail -f ~/.config/cc-memory/logs/worker.log

# 每次 hook 拆离日志（按时间戳）
ls -lt ~/.config/cc-memory/logs/run-*.log | head

# 手动触发一次（绕过 CC）
echo '{"session_id":"manual","transcript_path":"/tmp/fake.jsonl","reason":"test","last_assistant_message":"足够长的烟雾测试消息，超过 min_assistant_chars 阈值才会触发 GLM 调用"}' \
    | bash ./memory_system/hooks/session_end.sh
sleep 2
tail ~/.config/cc-memory/logs/worker.log

# 搜不到？可能是 cwd 变了（搬过项目目录）：
python3 ./memory_system/cli/ccmem.py list -n 5             # 看最近几条的 cwd 字段
python3 ./memory_system/cli/ccmem.py find "<关键词>" --all  # 不限当前项目
```

完整架构见 [DESIGN.md](./DESIGN.md)。
