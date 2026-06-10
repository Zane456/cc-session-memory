# skills/

镜像 cc-memory 配套的 Claude Code skill 文件，方便版本控制 + 在新机器上重装。

Claude Code 实际加载位置：`~/.claude/skills/<skill-name>/SKILL.md`，repo 里的副本仅作模板。

## 安装

把任一 skill 复制到 Claude Code 的 skills 目录，然后把 `<CC_MEMORY_REPO>` 占位符替换为本 repo 在你机器上的绝对路径：

```bash
REPO="$(pwd)"                                     # 在 cc-project-memory repo 根目录跑
mkdir -p ~/.claude/skills/sess
sed "s|<CC_MEMORY_REPO>|$REPO|g" \
    skills/sess/SKILL.md > ~/.claude/skills/sess/SKILL.md
```

之后开新 Claude Code session 就能直接用 `/sess`。

## 改 skill 后回写到 repo

skill 是直接在 `~/.claude/skills/<name>/SKILL.md` 编辑的；改完想同步回 repo：

```bash
REPO="$(pwd)"
sed "s|$REPO|<CC_MEMORY_REPO>|g" \
    ~/.claude/skills/sess/SKILL.md > skills/sess/SKILL.md
```

（即把绝对路径换回占位符再写入）。

## 当前内容

- `sess/SKILL.md` — `/sess` 历史会话检索 skill。两层数据：cc-memory GLM 摘要 + CC 原始 jsonl。`--raw` 模式直接读 `~/.claude/projects/<sid>.jsonl` 拿原话。
- `ccskill/SKILL.md` — `/ccskill` skill 使用统计（单命令，输出全部记录时间范围的调用排行）。
- `ccmcp/SKILL.md` — `/ccmcp` MCP 使用统计（单命令，按 server 排行 + top tools）。
