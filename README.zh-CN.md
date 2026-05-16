[English](README.md) | 简体中文

<div align="center">

<img src="docs/images/hero-zh.png" alt="cc-memory：给 Claude Code 装上长期记忆——后台自动保存，按需随时召回" width="100%">

# cc-memory

*因为你的 AI 助手不应该每次关掉终端就失忆。*

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)
![代码行数](https://img.shields.io/badge/代码-1800+-informational)
![LLM](https://img.shields.io/badge/LLM-任意-success)

**Claude Code 的会话记忆插件。**

它会在后台默默看着你的终端。对话一结束，自动帮你记住。
下次开会话，你只需说一句 `/sess`，一切就都回来了。

</div>

---

## 看效果

假设你今天正在做一个项目，告诉了 Claude Code 你的数据库架构。

```bash
你：  /sess
Claude Code：
  ✅ 已加载 12 条历史记忆（当前项目）。
  [最新 - 10月12日] 用户偏好使用 PostgreSQL 而非 MySQL...
  [10月11日] 数据库架构已确定：users, orders, products...

你：现在开始写用户认证接口。

Claude Code：开始编写...（已经知道你用 PostgreSQL，无需再问一遍！）
```

> 💡 Claude Code 关了、开了、崩溃了——都无所谓。你手里握着 `/sess` 这个开关，它对当前项目的一切记忆随叫随到。

这不是向量数据库在做跨项目语义搜索。
这是 **一个会话一个 Markdown 文件**，存在你的项目目录下，天生就能用关键词搜。

---

## 你为什么需要它？

如果你在用 Claude Code，你一定经历过这种痛：

| 痛点 | 现实 |
| :--- | :--- |
| 聊了 1 小时的架构设计 | 关掉终端，全没了 |
| 第二天重新打开 | "你好！我是 Claude，今天能帮你什么？" |
| 又得把项目背景从头解释一遍 | 每次、每次、每次…… |

**cc-memory 把这个问题一次性解决了。**

它让 Claude Code 从"只有 7 秒记忆的金鱼"，变成一个真正记得住你项目的搭档。

---

## 核心亮点

所有功能都围绕一个中心：**你来决定什么时候记，它负责记得完美。**

| 特性 | 对你意味着什么 | 怎么做到的 |
| :--- | :--- | :--- |
| **保存全靠自动** | 你只管聊天，结束它自己存 | 对话结束时后台 Python 进程自动触发 |
| **总结成笔记** | 一轮对话变成一份 200-300 字的干净笔记 | LLM 读取聊天记录，提取核心信息 |
| **加载由你定** | 你不说 `/sess`，记忆绝不会自己蹦出来 | 手动拉取，绝不自动注入 |
| **项目隔离** | A 项目的记忆不会出现在 B 项目 | 按当前项目目录自动隔离 |
| **稳如磐石** | 就算电脑崩溃，最多丢 1 轮对话 | 先保存再总结 |
| **任何模型都行** | OpenAI、Anthropic、DeepSeek 或本地 Ollama | 支持 9+ 家 LLM 提供商 |

---

## 它是怎么工作的（人话版）

没有什么魔法。就 3 步：

**1. 正常聊天**
你和 Claude Code 聊天。聊完关闭会话……

**2. 后台自动存档**
一个后台脚本自动触发。它把聊天记录交给 LLM（模型你自己选），LLM 写一份 200-300 字的总结笔记，存成 Markdown 文件。

**3. 你决定何时回忆**
下次打开 Claude Code，输入 `/sess`。它去找到当前项目的历史笔记，喂给 Claude。你什么都不说，Claude 就是一张白纸——清清爽爽。

---

## 一切用数字说话

不画饼。每个功能都是量化的：

| 指标 | 数值 | 意味着什么 |
| :--- | :--- | :--- |
| 安装时间 | 约 3 分钟 | 克隆下来跑个脚本，或者直接让 Claude Code 帮你装 |
| 外部依赖 | **0 个** | 纯 Python，开箱即用 |
| 代码总量 | 约 1,800 行 | Python + Bash，没有臃肿框架 |
| 每轮总结长度 | 200-300 字 | 只要精华，不要废话 |
| 存储上限 | 200 MB | 够存好几年的对话 |
| 永不删除 | 最新 10 条 | 哪怕超了容量，最近 10 条也永远留着 |
| 崩溃数据丢失 | ≤ 1 轮 | 就算突然断电，最多丢最后一次对话 |
| 命令行工具 | 10 个 | 列出、搜索、查看、清理……一切尽在掌握 |

---

## 10 个命令行工具

一切尽在掌握。没有黑盒。

| 命令 | 干什么用 |
| :--- | :--- |
| **`list`** | 看看一共存了多少条记忆 |
| **`here`** | 看看当前项目有几条记忆 |
| **`search`** | 按关键词搜记忆 |
| **`show`** | 查看某一条记忆的完整内容 |
| **`path`** | 看看记忆文件存在哪了 |
| **`latest`** | 看最新的一条记忆笔记 |
| **`stats`** | 看看用了多少空间、多少条记忆 |
| **`prune`** | 手动清理老记忆 |
| **`last-session`** | 看上一次会话的记录 |
| **`find`** | 按条件找某一条记忆 |

---

## 安装

**推荐：让 Claude Code 帮你装**（~3 分钟）：

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
claude
```

把 [INSTALL.md](INSTALL.md) 里的安装提示粘进去——Claude Code 会自动跑 `setup.sh`、配置你的 LLM、端到端验证。

**或者自己动手：**

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
./memory_system/bin/setup.sh --global --key <你的-LLM-api-key>
```

---

## 两个反共识的选择

灵感来自 [claude-mem](https://github.com/thedotmack/claude-mem)，但做了两个跟主流相反的决定：

| 主流做法 | 我的选择 | 为什么？ |
| :--- | :--- | :--- |
| 跨项目共享记忆 | **按项目目录隔离** | A 项目是做网站，B 项目是写脚本。混在一起只会添乱。 |
| 开新会话自动注入历史 | **用户用 `/sess` 手动拉取** | 有时候你想要一个干净的开始。应该由你来决定 Claude 什么时候需要"回忆"。 |

完整架构见 [DESIGN.md](DESIGN.md)。

---

<div align="center">

> *「最好的工具不会告诉你该怎么做。它们只是在你需要的时候，刚好在那儿。」*

<br>

**Zane456** — AI 工具链构建者 & 电力电子研究者

| 平台 | 链接 |
| :--- | :--- |
| 🌐 GitHub | [Zane456](https://github.com/Zane456) |
| 𝕏 X / Twitter | [@ZaneZaneZzZZ](https://x.com/ZaneZaneZzZZ) |
| 📕 小红书 | [Zz302179383](https://www.xiaohongshu.com/user/profile/Zz302179383) |
| ✉️ Email | zz302179383@gmail.com |

<br>

⭐ 如果这个工具帮到了你的 Claude Code 工作流，给个 star——让更多人看到。

<br>

MIT License © [Zane456](https://github.com/Zane456)

</div>
