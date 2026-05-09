# 安装 cc-memory

整套系统装好需要 4 块东西：

1. **`~/.config/cc-memory/config.json`** — 你的 z.ai API key 和模型配置（**不在 repo，必须本机生成**）
2. **`~/.claude/settings.json` 的 Stop hook** — 让 Claude Code 每轮调本系统的 worker
3. **`/sess` skill 装到 `~/.claude/skills/sess/`** — 语言触发的会话检索能力
4. **（可选）`~/.claude/projects/` 容量上限** — 用 cron/launchd 定时跑 `prune_cc_transcripts.py`

下面 2 种装法，**任选其一**。

---

## 方式 A：让 Claude Code 自己装（推荐，3 分钟）

**前置**：

- 装好 [Claude Code](https://docs.claude.com/en/docs/claude-code/quickstart)
- 在 [z.ai](https://z.ai) 拿到 API key（`glm-5-turbo` 模型每千 token 大约 ¥0.001，每轮总结 < ¥0.001）

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
claude
```

进 Claude Code 后，把下面这段一字不漏粘进去：

> 请按本 repo 根目录的 `INSTALL.md` 第 3 节"标准化安装步骤"帮我装 cc-memory。
> 我用全局模式（`--global`）。每完成一步给我打勾汇报，遇到任何意外停下来问我。
> 装完跑一次冒烟测试，确认 hook 触发、memories md 生成、`/sess` skill 加载正常。
> 我的 z.ai API key：`<把你的 key 粘到这里>`

Claude 会自己跑 `setup.sh --global --key <你的 key>`、验证 hook 已注册、确认 skill 文件已落地、跑一次端到端冒烟。任何意外它会停下来问你。

---

## 方式 B：手动装

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
./memory_system/bin/setup.sh --global --key <你的-z.ai-key>
```

`setup.sh --global` 一次完成：

| 步骤 | 写到哪 | 备注 |
|---|---|---|
| 创建 config | `~/.config/cc-memory/config.json` (chmod 600) | 已有则保留，加 `--force` 覆盖（先备份 .bak）|
| 注册 Stop hook | `~/.claude/settings.json` | merge 到现有 hooks，不破坏你别的配置；旧文件备份 .bak |
| 装 slash 命令 | `~/.claude/commands/{recall,sess}.md` | 老入口，向后兼容 |
| 装 sess skill | `~/.claude/skills/sess/SKILL.md` | 新入口，从 `skills/sess/SKILL.md` 模板生成，自动把 `<CC_MEMORY_REPO>` 替换成本机绝对路径 |
| 给可执行位 | hook + cli | |

装完进任意项目目录跑 `claude`，每轮回完话都会后台异步总结到 `<repo>/memories/YYYY-MM-DD-<sid>.md`。

**卸载**：`./memory_system/bin/setup.sh --unregister-global` 移除 hook（保留你别的配置不动）。skill / 命令 / 配置文件需手动删。

---

## 标准化安装步骤（给 Claude Code 看 / 也可手动）

### 1. 检查依赖

```bash
python3 --version       # 需要 3.8+
which claude            # 需要 Claude Code 已装
```

### 2. 跑 setup.sh

```bash
./memory_system/bin/setup.sh --global --key <ZAI_KEY>
```

期望输出：`✓ Stop hook 写入 ...`、`✓ slash 命令写入 ...`、`✓ /sess skill 写入 ...`、`✓ 全局安装完成`。

### 3. 验证 4 个落地点

```bash
test -f ~/.config/cc-memory/config.json && echo "✓ config"
grep -q "session_end.sh" ~/.claude/settings.json && echo "✓ hook 已注册"
test -f ~/.claude/skills/sess/SKILL.md && echo "✓ /sess skill 已装"
test -f ~/.claude/commands/sess.md && echo "✓ /sess 命令已装"
```

四个 `✓` 都打出来才算真装好。

### 4. 冒烟测试（不开 Claude Code 的话）

```bash
echo '{"session_id":"smoke-test","transcript_path":"/tmp/none","reason":"manual","last_assistant_message":"this is a smoke test message that exceeds 50 chars to ensure the worker actually fires GLM"}' \
    | bash memory_system/hooks/session_end.sh
sleep 5
tail -20 ~/.config/cc-memory/logs/worker.log
```

worker.log 应当看到 `calling GLM (turn): model=glm-5-turbo` + `GLM ok in X.XXs`。

如果 GLM 报错（比如 API key 写错、网络不通），错误会落到 `~/.config/cc-memory/failures/`，不会污染 memories/。

### 5. 端到端

```bash
cd ~/任意项目
claude
> 给我讲个笑话                          # 随便聊一轮
> /exit
ls -lt ~/.config/cc-memory/logs/        # 应当有新的 run-*.log 和 worker.log 增长
ls -lt <repo>/memories/                 # 应当有今天日期的 .md 文件
```

下次再开 CC 在这个目录里：

```
> /sess                                  # 加载上次会话摘要
> /sess <关键词>                         # 搜某个话题（当前项目）
> 上次那个 bug 的原话是什么              # 自动触发 sess skill 走 --raw
```

---

## 可选：自动定期清理 `~/.claude/projects/`

Claude Code 自己每轮把完整 transcript 写在 `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`，长期会膨胀（cc-memory 的 GLM 摘要不依赖它，但 `/sess --raw` 模式依赖；删掉旧的就只能看摘要）。

repo 自带 `memory_system/bin/prune_cc_transcripts.py`，默认 3 GB 上限、保护最近 24 小时活跃 session：

```bash
# 先 dry-run 看会删什么
python3 memory_system/bin/prune_cc_transcripts.py --dry-run

# 真删
python3 memory_system/bin/prune_cc_transcripts.py
```

要每天自动跑一次（macOS launchd），告诉 Claude Code：

> 帮我用 launchd 配一个每天 22:00 自动跑 `<repo>/memory_system/bin/prune_cc_transcripts.py` 的 plist，标签叫 `com.cc-memory.prune-transcripts`。

或自己手写 plist 放到 `~/Library/LaunchAgents/`。

---

## 故障排查

```bash
# 看 worker 日志
tail -f ~/.config/cc-memory/logs/worker.log

# 看本次 hook 拆离日志（按时间戳）
ls -lt ~/.config/cc-memory/logs/run-*.log | head

# 手动触发 worker（绕过 CC）
echo '{"session_id":"manual-test","transcript_path":"/tmp/fake.jsonl","reason":"manual"}' \
    | bash ./memory_system/hooks/session_end.sh

# 搜不到记忆？可能 cwd 不匹配（搬目录了）
python3 ./memory_system/cli/ccmem.py list -n 5      # 看最新几条的 cwd 字段
python3 ./memory_system/cli/ccmem.py find "<kw>" --all   # 不限当前项目
```

详细架构见 [DESIGN.md](./DESIGN.md)。
