---
name: ccmcp
description: 查 MCP 工具使用统计。单命令、无参数：固定输出目前记录的全部时间范围内所有 MCP server 的调用排行（次数/首次/最近/top tools）。触发：用户输 /ccmcp，或问"哪些 MCP 用得多"、"MCP 调用次数"、"哪些 MCP 在吃灰"、"MCP 使用统计"。查 skill 用 ccskill，不归这里。
---

# /ccmcp — MCP 使用统计（单命令）

数据源：`<cc-memory>/memories/skill_usage.jsonl`（source=mcp 的记录），cc-memory 的 Stop hook 每轮自动记录，长期累积，不受 CC 30 天 transcript 清理影响。

## 行为（固定，无参数分支）

跑这一条命令，把输出整理给用户：

```bash
python3 "<CC_MEMORY_REPO>/memory_system/cli/ccmem.py" mcp-stats
```

输出即全部记录时间范围的排行：MCP server / 次数 / 首次 / 最近 / top tools（每个 server 内调用最多的 3 个工具）。

## 输出要求

- 直接给表格 + 一句话总结（总数、最常用、谁在吃灰），不要复述命令本身

## 数据维护（仅用户提到"数据不全/补一下/回填"时）

```bash
... mcp-stats --backfill
```
