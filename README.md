# CC project memory

一套为 Claude Code 设计的轻量级会话记忆系统。**Claude 每回完一轮**通过 `Stop` hook 触发，
**用 GLM (z.ai) API 总结这一轮**（省 token），**完全后台异步**不阻塞 CC，**append 到当 session 的 md 文件**。
**不在 SessionStart 自动加载**，需要时通过 `/recall` 搜索或 `/sess` 加载上一次 session 全文。

设计灵感来自 [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)，但做了三处简化 + 一处不同：

| 维度 | claude-mem | 本项目 |
|---|---|---|
| Hook 数量 | 5 个（SessionStart/UserPromptSubmit/PostToolUse/Stop/SessionEnd） | 1 个（**仅 Stop，每轮 append**）|
| 写入时机 | session 期间持续观察 | **每轮一次写入**，CC 怎么挂都最多丢未完成那轮 |
| 总结引擎 | Claude agent-sdk | **z.ai GLM API**（省 token） |
| 启动注入 | SessionStart 自动注入历史 | **不注入**，手动 `/recall` 或 `/sess` |
| 存储 | SQLite + Chroma 向量库 | **markdown 文件 + grep** |

## 目录结构

```
.
├── .claude/
│   ├── settings.json           # SessionEnd hook 配置
│   └── commands/recall.md      # /recall slash 命令
├── memory_system/
│   ├── hooks/
│   │   ├── session_end.sh      # bash 拆离器（≈10ms 返回）
│   │   └── summarize.py        # python worker（调 GLM、写 md）
│   ├── cli/ccmem.py            # 检索 CLI
│   ├── config/config.example.json
│   ├── bin/setup.sh            # 一次性安装脚本
│   └── README.md
├── memories/                    # 总结产物（已 .gitignore）
└── DESIGN.md                    # 完整设计方案
```

## 快速开始

### 1. 安装配置（一次性）

**推荐：全局安装**（在任意项目目录跑 CC 都生效）：

```bash
cd /path/to/cc-project-memory     # 你 clone 这个 repo 后的位置
./memory_system/bin/setup.sh --global --key <你的-z.ai-key>
```

这会：
- 创建 `~/.config/cc-memory/config.json`（默认 `model = glm-5-turbo`、`thinking_enabled = false`、自动注入 `memories_dir`），`chmod 600`
- 把 Stop hook 段 **merge** 到 `~/.claude/settings.json`（保留你已有的 hook / permissions 不破坏；旧文件备份成 `.bak`）
- 把 `recall.md` / `sess.md` 复制到 `~/.claude/commands/`（用 cc-memory 的安装绝对路径，跨项目可用）
- 给 hook / cli 设可执行位

**仅项目级**（不带 `--global`）：hook 只注册在本项目的 `.claude/settings.json`，离开本目录就没记忆。适合"只想给一个项目用"的场景。

**自定义 memories 目录**：加 `--memories-dir ~/cc-memory-data` 把记忆写到别处。

**卸载全局 hook**：

```bash
./memory_system/bin/setup.sh --unregister-global
```

会把 cc-memory 的 hook 段从 `~/.claude/settings.json` 干净移除，保留你其它的 hook / permissions 不动。slash 命令文件和 `~/.config/cc-memory/` 配置不动，要彻底清需要手动 `rm`。

**已存在配置时**：脚本不覆盖，展示当前 model / thinking_enabled / memories_dir，提示加 `--force` 才重新生成（会备份 `.bak`，自定义字段保留）。

想换 model / endpoint / 打开 thinking 直接编辑 `~/.config/cc-memory/config.json` 即可。

### 2. 验证 hook 已注册

在这个目录里启动一次 Claude Code：

```bash
claude
# ……聊几轮……
# /exit
```

退出后等几秒，看：

```bash
ls -la ~/.config/cc-memory/logs/    # 应该有 worker.log
ls -la ./memories/                  # 应该有 2026-05-09-xxxxxx.md
```

### 3. 检索

CLI：

```bash
python3 ./memory_system/cli/ccmem.py list -n 10              # 列最近 N 条（全局）
python3 ./memory_system/cli/ccmem.py here                    # 当前项目目录的记忆
python3 ./memory_system/cli/ccmem.py search "GLM endpoint"   # 全局正则搜索
python3 ./memory_system/cli/ccmem.py search "GLM" --here     # cwd-scoped 搜索
python3 ./memory_system/cli/ccmem.py show 8a4b3c2d           # 看一条全文
python3 ./memory_system/cli/ccmem.py latest                  # 最新一条全文
python3 ./memory_system/cli/ccmem.py last-session            # 当前项目最近 1 条，带 LLM-friendly 边界
python3 ./memory_system/cli/ccmem.py stats                   # 总条数 / 占用 / 上限
python3 ./memory_system/cli/ccmem.py prune                   # 手动剪枝
```

或者在 Claude Code 里：

```
/recall GLM endpoint        # cwd 范围搜索；加 --all 切全局
/sess                       # 加载当前项目最近一次会话作为上下文
```

## 关键设计决策

| 问题 | 选择 | 理由 |
|---|---|---|
| Stop vs SessionEnd | **Stop（每轮 append）** | 增量、可靠：CC 关窗 / Cmd+Q / 崩溃都最多丢"未完成的最后一轮"。SessionEnd 反而不一定触发（见 DESIGN §3）|
| 死循环防护 | 检测 `stop_hook_active=true` 就 return | 避免 hook 自身引发的 Stop 再触发 worker 无限套娃（CC 文档明确警告）|
| 阻塞 vs 异步 | `nohup setsid python3 ... & disown` | bash 立即 exit 0，python 独立会话继续跑 |
| 同步还是 stdin pipe | 写 tmpfile 传参 | pipe 在父进程退出时会断，文件最稳 |
| 文件并发 | `fcntl.flock(LOCK_EX)` | 两个 Stop 几乎同时触发也不会相互覆盖 |
| 存储格式 | markdown + frontmatter（一 session 一文件，多轮次段）| grep 友好，人眼可读，无依赖 |
| 容量上限 | `max_db_size_mb=200`，FIFO 剪枝到 90%，**最新 10 条永不删** | 防止长期累积爆盘 |
| 配置位置 | `~/.config/cc-memory/config.json` | 用户私有，不进 repo |
| 失败处理 | GLM 失败 → append 一段 `## ⚠️ GLM 失败` 到当 session 文件 | 永远 `exit 0`，不影响 CC |

## 安全

- **API key 不入 git**：存在 `~/.config/cc-memory/config.json`，权限 600；`.gitignore` 也兜底忽略 `config.json`
- **memories 默认不入 git**（`.gitignore` 排除 `*.md`）；如要团队共享，手动调整
- **日志不含 key**，但建议定期清理 `~/.config/cc-memory/logs/`

## 故障排查

```bash
# 看 worker 日志
tail -f ~/.config/cc-memory/logs/worker.log

# 看本次 hook 拆离日志（按时间戳）
ls -lt ~/.config/cc-memory/logs/run-*.log | head

# 手动触发一次（不通过 Claude Code）
echo '{"session_id":"manual-test","transcript_path":"/tmp/fake.jsonl","reason":"manual"}' \
    | bash ./memory_system/hooks/session_end.sh
sleep 2
tail ~/.config/cc-memory/logs/worker.log
```

详细设计见 [DESIGN.md](./DESIGN.md)。
