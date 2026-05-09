# cc-memory

> 🌐 **English** · [中文](./README.zh.md)

A lightweight per-turn session memory system for Claude Code. After every Claude turn, a `Stop` hook fires, a detached Python worker calls the **GLM API at z.ai** to summarize that turn (cheap), and appends it to a per-session markdown file. Nothing auto-loads on the next session — you pull memory in **explicitly** via `/sess`, or by saying things like *"what was the original wording last time?"* (the `sess` skill auto-triggers `--raw` mode).

> 📦 **Want to install?** See [INSTALL.md](./INSTALL.md) — recommended path: *let Claude Code install it for you*, ~3 minutes.

![Architecture](./docs/images/architecture.png)

## Why these tradeoffs

![Philosophy](./docs/images/philosophy.png)

Inspired by [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem), with two intentional differences:

| Dimension | claude-mem | cc-memory |
|---|---|---|
| Hook count | 5 (SessionStart / UserPromptSubmit / PostToolUse / Stop / SessionEnd) | **1 (Stop, per-turn append)** |
| Write timing | Continuous observation during session | **Once per turn** — at most loses the unfinished last turn |
| Summary engine | Claude agent-sdk | **z.ai GLM API** (cheap) |
| SessionStart auto-inject | Yes | **No** — manual `/sess` |
| Storage | SQLite + Chroma vector DB | **Markdown files + grep** |

### 1. No cross-project memory by default ❌

claude-mem's vector DB does "search across all projects" semantic retrieval — sounds cool. But people who actually use Claude Code seriously already organize their work per-project (each has its own `CLAUDE.md`, docs, code). Cross-project search retrieves mostly **false-positive** signals (keyword collisions, same-name-different-meaning concepts) that dilute the current project's signal.

→ cc-memory isolates by `cwd` by default. `/sess` looks in the current project first; `--all` extends globally. **No assumption that you need cross-project context.**

### 2. No SessionStart auto-inject ❌

claude-mem stuffs the previous session's summary into the context window every time you start a new session. **But context window is a scarce resource.** Not every session needs the previous one's history. Auto-injection means paying a "context-you-might-not-need" tax every single time, diluting the current task's signal; in long conversations this tax forces premature compaction, losing more important info.

Worse, it **takes the judgment away from you**: even *"do I want history this time"* gets decided for you.

→ cc-memory makes loading **explicit**: writes are background-automatic (per-turn append), but reads you trigger — `/sess` to continue from last time, `/sess <keyword>` to dig up a topic. When you don't need it, memories sit quietly on disk.

These two tradeoffs in one line: **Write should be automatic and cheap; read should be explicit and controlled.**

## Two-layer storage

cc-memory writes its own GLM summaries; **Claude Code itself separately writes the full raw transcript** (its `/resume` and `/continue` features depend on this). cc-memory's `--raw` mode reads that.

| Layer | Where | Format | Per turn | Read via |
|---|---|---|---|---|
| GLM summary (lossy, fast) | `<repo>/memories/YYYY-MM-DD-<sid>.md` | markdown + frontmatter | ~300 chars | `ccmem find / last-session`, `/sess` |
| CC raw transcript (lossless, large) | `~/.claude/projects/<encoded-cwd>/<sid>.jsonl` | line-delimited JSON | full text + tool I/O | `ccmem ... --raw`; `/sess` auto-triggers `--raw` on phrases like *"exact wording / specifics / details"* |

The raw transcripts grow unboundedly (Claude Code never trims them). cc-memory ships `memory_system/bin/prune_cc_transcripts.py` to cap `~/.claude/projects/` at 3 GB (configurable), oldest first, protecting files modified in the last 24 h.

## Quick start

See [INSTALL.md](./INSTALL.md) for the full guide. TL;DR:

```bash
git clone https://github.com/Zane456/cc-project-memory.git
cd cc-project-memory
./memory_system/bin/setup.sh --global --key <your-z.ai-key>
```

CLI cheatsheet:

```bash
python3 memory_system/cli/ccmem.py last-session              # last session in current project (summary)
python3 memory_system/cli/ccmem.py last-session --raw        # ... but read CC's raw .jsonl instead
python3 memory_system/cli/ccmem.py find "<keyword>"          # search current project's summaries
python3 memory_system/cli/ccmem.py find "<keyword>" --raw    # search the raw .jsonl directly
python3 memory_system/cli/ccmem.py find "<keyword>" --all    # extend to global
python3 memory_system/cli/ccmem.py stats                     # disk usage
python3 memory_system/cli/ccmem.py prune                     # manual FIFO prune of summaries

python3 memory_system/bin/prune_cc_transcripts.py --dry-run  # cap ~/.claude/projects at 3 GB
```

In Claude Code:

- `/sess` — load last session in current project
- `/sess <keyword>` — search summaries for that keyword
- *"what was the exact wording last time?"* — the `sess` skill detects detail-seeking phrases and switches to `--raw`

## Repository layout

```
.
├── INSTALL.md                            # install guide (recommended entry)
├── DESIGN.md                             # full architecture spec
├── memory_system/
│   ├── hooks/
│   │   ├── session_end.sh                # bash detacher (~10 ms return)
│   │   └── summarize.py                  # python worker (GLM call, md append)
│   ├── cli/ccmem.py                      # retrieval CLI
│   ├── bin/
│   │   ├── setup.sh                      # one-shot installer
│   │   └── prune_cc_transcripts.py       # cap ~/.claude/projects at 3 GB
│   └── config/config.example.json
├── skills/                               # ~/.claude/skills/ mirror (template)
│   ├── README.md                         # install / sync instructions
│   └── sess/SKILL.md                     # /sess language-trigger skill
├── memories/                             # GLM summaries (gitignored)
└── docs/images/                          # the diagrams above
```

## Key design decisions

| Question | Choice | Why |
|---|---|---|
| `Stop` vs `SessionEnd` hook | **Stop** (per-turn append) | Incremental, reliable: window close / `Cmd+Q` / crash all lose at most the unfinished last turn. `SessionEnd` is not always triggered (see DESIGN §3). |
| Loop protection | Detect `stop_hook_active=true` and exit | Prevent hook from self-triggering Stop infinitely (CC docs warn explicitly). |
| Blocking vs async | `nohup setsid python3 ... & disown` | Bash exits immediately (~10 ms); Python keeps running detached. |
| Tmpfile vs stdin pipe | Tmpfile | Pipes break when parent exits; tmpfile is robust. |
| File concurrency | `fcntl.flock(LOCK_EX)` | Two near-simultaneous Stops can't overwrite each other. |
| Storage format | Markdown + frontmatter (one session = one file with multiple turn sections) | grep-friendly, human-readable, no dependencies. |
| Summary length | ~300 chars / turn (`max_tokens=600`) | Detailed enough that another model can read just the summary and know what happened, including failed attempts. |
| Capacity cap | `max_db_size_mb=200`, FIFO prune to 90 %, **never delete the newest 10** | Prevent unbounded disk growth. |
| Config location | `~/.config/cc-memory/config.json` (chmod 600) | User-private, not in repo. |
| Failure handling | GLM error → log to `~/.config/cc-memory/failures/`, never propagate to CC | Always `exit 0`. |

## Security

- **API key never enters git**: stored in `~/.config/cc-memory/config.json` (chmod 600); `.gitignore` also catches `**/config.json` as a safety net.
- **`memories/` is gitignored by default.** To version-control, point it at a separate private repo (this author's setup uses [Zane456/my-project-memory](https://github.com/Zane456)) or remove the gitignore entry.
- **Logs do not contain the API key**, but periodically clean `~/.config/cc-memory/logs/`.

## Troubleshooting

```bash
# worker logs
tail -f ~/.config/cc-memory/logs/worker.log

# per-invocation detacher logs
ls -lt ~/.config/cc-memory/logs/run-*.log | head

# manual trigger (bypassing Claude Code)
echo '{"session_id":"manual","transcript_path":"/tmp/fake.jsonl","reason":"test","last_assistant_message":"a smoke-test message long enough to clear the min_assistant_chars threshold"}' \
    | bash ./memory_system/hooks/session_end.sh
sleep 2
tail ~/.config/cc-memory/logs/worker.log

# searched but found nothing? cwd may have moved:
python3 ./memory_system/cli/ccmem.py list -n 5             # check the cwd field
python3 ./memory_system/cli/ccmem.py find "<kw>" --all     # extend to global
```

Full architecture: [DESIGN.md](./DESIGN.md).
