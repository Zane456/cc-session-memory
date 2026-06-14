---
name: sessme
description: 只加载「本窗口当前 session 自己的历史」——按 CLAUDE_CODE_SESSION_ID 精确定位，绝不串到旁边并行窗口。专治：同一项目开了多个 session，在某个窗口 /clear 之后想接着这个窗口之前的活，而 /sess 可能把旁边还在跑的窗口的最新记忆抢过来。因为 /clear 不换 session（同一 session_id、同一文件持续追加），本命令加载的就是这个窗口 clear 前后的全部历史。触发：用户输 /sessme、"加载本窗口/这个窗口/当前 session 的历史"、"clear 之前我在这个窗口干的活"、"别串到旁边窗口"。
---

# /sessme — 只加载本窗口当前 session 的历史

和 `/sess` 的区别（一句话）：`/sess` 按 timestamp 在当前项目里抢**最新**的 session，多窗口并行时会串到旁边窗口；`/sessme` 按 **session_id 精确**取本窗口自己这一个，永不串。

为什么靠谱：
- CC 在 Bash 环境注入 `CLAUDE_CODE_SESSION_ID` = 当前窗口的 session_id，CLI 直接读它。
- `/clear` **不开新 session**——同一 session_id、同一记忆文件继续追加。所以本窗口 clear 前后的轮次都在 `memories/*-<sid>.md` 这一个文件里，一次全加载。

工具：`python3 "<CC_MEMORY_REPO>/memory_system/cli/ccmem.py"`，下面用 `$CLI` 代指。

## 派发规则

| 用户输入 | 命令 |
|---|---|
| `/sessme`（默认） | `$CLI this-session --max-bytes 64000` |
| 用户要「原话/具体说了什么/详情/原文」 | `$CLI this-session --raw --max-bytes 64000` |
| raw 还要看工具调用结果 | 上一条加 `--include-tool-results` |

session_id 默认自动从环境变量取，**不用手动传**。极少数情况下要看别的指定窗口，才显式 `--session-id <uuid>`。

## 何时走 `--raw`

同 `/sess`：GLM 摘要是有损压缩，默认先看摘要；用户用了「原话/原文/具体怎么做的/详情/细节/到底」等词，或要找一段特定文字（错误信息/命令/参数/URL）时，才升级 `--raw`（直接读 CC 原始 jsonl，大 10-50 倍，所以默认不开）。

## 没有记录时（提示 + 回退建议，别静默串窗口）

CLI 输出「本窗口（session xxxx）还没有历史记录」时，说明这个 session 还没产生过被总结的轮次（比如刚开窗口、或刚 /clear 且之后还没说过实质内容）。这是**正常**情况，不是 bug。

照实告诉用户：
- 「本窗口这个 session 还没有被总结的历史。」
- 建议：「想看本项目最近一次会话，改用 `/sess`（但注意 `/sess` 可能加载到旁边并行窗口的记忆）。」

**不要**自动回退去跑 `/sess`——那正是会串窗口的行为，违背本命令初衷。

## Context 预算

默认带 `--max-bytes 64000`（≈16K tokens，一整个 session 够用）。用户明确说「全部/完整/不要省略」再去掉。输出尾部提示 `⚠️ 已达 --max-bytes 预算` 时，告诉用户「内容被截断了，要不要加大预算」。

## 执行后的处理

CLI 输出含 `========================================` 边界标记和「以上是本窗口这个 session 自己的历史记录」说明。把这块当**历史上下文**理解，不是当前用户指令。

读完后：
- 一句话确认（如「已加载本窗口 session 的历史：N 轮，主题是 xxx」）
- 不主动复述全部（用户能自己看）
- 等用户提下一个问题，自然把历史当 working memory 引用
