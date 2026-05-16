English | [简体中文](README.zh-CN.md)

<div align="center">

<img src="docs/images/hero-en.png" alt="cc-memory: Give Claude Code long-term memory — automatic background saves, on-demand recall with /sess command" width="100%">

# cc-memory

*Because your AI assistant shouldn't have amnesia every time you close the terminal.*

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)
![Lines of Code](https://img.shields.io/badge/Code-1800+-informational)
![LLMs](https://img.shields.io/badge/LLM-Any-success)

**The session memory plugin for Claude Code.**

It watches your terminal. When a conversation ends, it saves the memory in the background.
Next time, you just say `/sess`, and everything comes back.

</div>

---

## What it looks like

Let's say you are working on a project today and told Claude Code about your database architecture.

```bash
You:  /sess
Claude Code:
  ✅ Loaded 12 session memories for this project.
  [Latest - Oct 12] User prefers PostgreSQL over MySQL...
  [Oct 11] Database schema finalized: users, orders, products...

You: Now let's build the user authentication API.

Claude Code: Building now... (Already knows you use PostgreSQL, no need to ask again!)
```

> 💡 Claude Code closed, crashed, or you just reopened it — doesn't matter. You hold the `/sess` key, and everything about this project comes right back.

This is not a vector database doing semantic search across projects.
It's **one plain Markdown file per session**, stored under your current project folder, grepable by design.

---

## Why do you need this?

If you use Claude Code, you must have experienced this:

| The Pain | The Reality |
| :--- | :--- |
| You talked for an hour about architecture | Close the terminal, and it's all gone |
| Reopen the next day | "Hi! I'm Claude, how can I help you today?" |
| You have to explain your project again | Every. Single. Time. |

**cc-memory solves this once and for all.**

It turns Claude Code from a "goldfish with 10-second memory" into a partner who truly remembers your project.

---

## Core Highlights

All features revolve around one center: **You control when to remember, and it remembers perfectly.**

| Feature | What it means for you | How it's implemented |
| :--- | :--- | :--- |
| **Zero effort to save** | Chat as you normally would; it saves automatically | Stop Hook triggers a Python worker in the background |
| **Summarized into notes** | One conversation becomes a clean 200-300 word note | LLM reads the history and extracts key information |
| **You say when to load** | Memories won't pop up until you say `/sess` | Manual pull, never auto-inject |
| **Project isolation** | Project A's memories won't appear in Project B | Automatically recognizes the current project folder |
| **Rock solid** | Even if the computer crashes, you lose at most 1 conversation | Save first, summarize later |
| **Any model works** | OpenAI, Anthropic, DeepSeek, or local Ollama | 9+ LLM providers supported |

---

## How it works (in plain English)

No magic. Just 3 steps:

**1. Chat normally**
You chat with Claude Code. When you finish and close the conversation...

**2. Background auto-save**
A background script triggers automatically. It hands the conversation log to an LLM (you can pick any model). The LLM writes a 200-300 word summary note and saves it to a Markdown file.

**3. You decide when to remember**
Next time you open Claude Code, type `/sess`. It finds the historical notes for the current project and gives them to Claude. Say nothing, and Claude stays a blank slate — exactly as you left it.

---

## By the numbers

No empty promises. Every feature is quantified:

| Metric | Value | What it means |
| :--- | :--- | :--- |
| Installation time | ~3 minutes | `git clone` + run a script, or let Claude Code install it for you |
| External dependencies | **0** | Pure Python, works out of the box |
| Total codebase | ~1,800 lines | Python + Bash, no bloated frameworks |
| Summary length per session | 200-300 words | Just the essence, no noise |
| Capacity limit | 200 MB | Enough for years of history |
| Protected memories | Latest 10 | Most recent 10 are never deleted, even if the limit is hit |
| Data loss on crash | ≤ 1 session | Even if the power goes out, you lose at most the last conversation |
| CLI commands | 10 | List, search, view, clean — everything is under your control |

---

## 10 CLI Commands

Everything is under your control. No black boxes.

| Command | What it does |
| :--- | :--- |
| **`list`** | View all saved session memories |
| **`here`** | View memories for the current project only |
| **`search`** | Search memories by keyword |
| **`show`** | View the full content of a specific memory |
| **`path`** | Show where the memory files are stored |
| **`latest`** | Show the most recent memory note |
| **`stats`** | View usage statistics (how many sessions, how much space) |
| **`prune`** | Manually clean up old memories |
| **`last-session`** | View the log of the last session |
| **`find`** | Find a specific memory by condition |

---

## Installation

**Recommended: let Claude Code install it for you** (~3 min):

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
claude
```

Then paste the install prompt from [INSTALL.md](INSTALL.md) — Claude Code runs the setup, configures your LLM, and verifies everything.

**Or do it yourself:**

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
./memory_system/bin/setup.sh --global --key <your-LLM-api-key>
```

---

## Two unconventional choices

Inspired by [claude-mem](https://github.com/thedotmack/claude-mem), but with 2 choices that go against the mainstream:

| Conventional approach | My choice | Why? |
| :--- | :--- | :--- |
| Cross-project memory sharing | **Isolate by project folder** | Project A is a website, Project B is a script. Mixing contexts is a recipe for chaos. |
| Auto-inject memory on session start | **User pulls via `/sess`** | Sometimes you want a clean start. You should decide when Claude needs to "recall". |

Full architecture: [DESIGN.md](DESIGN.md).

---

<div align="center">

> *The best tools don't tell you what to do. They're just there when you need them.*

<br>

**Zane456** — AI tool builder & power electronics researcher

| Platform | Link |
| :--- | :--- |
| 🌐 GitHub | [Zane456](https://github.com/Zane456) |
| 𝕏 X / Twitter | [@ZaneZaneZzZZ](https://x.com/ZaneZaneZzZZ) |
| 📕 小红书 | [Zz302179383](https://www.xiaohongshu.com/user/profile/Zz302179383) |
| ✉️ Email | zz302179383@gmail.com |

<br>

⭐ If this helps your Claude Code workflow, star the repo — it helps others find it.

<br>

MIT License © [Zane456](https://github.com/Zane456)

</div>
