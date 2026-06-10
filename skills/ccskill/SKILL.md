---
name: ccskill
description: 查 skill 使用统计。单命令、无参数：固定输出目前记录的全部时间范围内所有 skill 的调用排行（次数/首次/最近/项目数）。触发：用户输 /ccskill，或问"哪些 skill 用得多"、"skill 调用次数"、"哪些 skill 在吃灰"、"skill 使用统计"。查 MCP 用 ccmcp，不归这里。
---

# /ccskill — skill 使用统计（单命令）

数据源：`<cc-memory>/memories/skill_usage.jsonl`，cc-memory 的 Stop hook 每轮自动记录，长期累积，不受 CC 30 天 transcript 清理影响。

## 行为（固定，无参数分支）

跑这一条命令，把输出整理给用户：

```bash
python3 "<CC_MEMORY_REPO>/memory_system/cli/ccmem.py" skill-stats
```

输出即全部记录时间范围的排行：skill / 次数 / 首次 / 最近 / 项目数（已自动过滤 /clear 等 CC 内置命令）。

## 输出要求

- 直接给表格 + 一句话总结（总数、最常用、谁在吃灰），不要复述命令本身
- 用户追问细节（某 skill、某项目、按天趋势）时，才可加 CLI 自带 flag：`--here` `--days N` `--by day` `--all`

## 数据维护（仅用户提到"数据不全/补一下/回填"时）

```bash
... skill-stats --backfill
```
