# Installing cc-session-memory

English | [简体中文](INSTALL.md)

A full install has 4 pieces:

1. **`~/.config/cc-session-memory/config.json`** — endpoint + model + api_key for the LLM provider you picked (OpenAI / Anthropic / DeepSeek / local Ollama / Z.AI / …). **Not in the repo — generated on your machine.**
2. **A Stop hook in `~/.claude/settings.json`** — makes Claude Code call this system's worker after every turn.
3. **Skills in `~/.claude/skills/`** — `/sess` (session recall) + `/sessme` (this window's own session only, never bleeds across parallel windows) + `/ccskill` `/ccmcp` (skill / MCP usage stats).
4. **(Optional) a size cap on `~/.claude/projects/`** — run `prune_cc_transcripts.py` on a schedule via cron/launchd.

Two ways to install — **pick one**.

---

## Method A: let Claude Code install it (recommended, 3 minutes)

**Prerequisites**:

- macOS or Linux (file locking uses `fcntl`; Windows is not supported)
- [Claude Code](https://docs.claude.com/en/docs/claude-code/quickstart) installed
- An API key from any LLM provider (see the [provider matrix](#provider-matrix) below for endpoint/model values)

```bash
git clone https://github.com/Zane456/cc-session-memory.git
cd cc-session-memory
./memory_system/bin/setup.sh --global    # prompts for your key; it only lands in a chmod-600 local config
claude
```

> ⚠️ **Never paste your API key into the Claude Code chat.** Everything you type is stored verbatim in the transcripts under `~/.claude/projects/` (and would even get summarized into memories/ by this very tool). The key's only correct entry point is `setup.sh`: the interactive prompt keeps it out of your shell history; for scripting use `--key <key>` or the `ZAI_API_KEY` environment variable.

Once inside Claude Code, paste this in verbatim:

> setup.sh already ran with my key. Please finish installing cc-session-memory following section 3
> ("Standardized install steps") of `INSTALL.en.md` in this repo, in global mode:
> verify the 4 install targets. My LLM provider is `<openai / anthropic / deepseek / openrouter / ollama / z.ai / …>` —
> if it's not OpenAI, adjust `endpoint` / `model` / `protocol` in `~/.config/cc-session-memory/config.json`
> per the provider matrix (leave api_key alone).
> Then run the smoke test once: confirm the hook fires, a memories md gets written, and the `/sess` skill loads.
> Report each step with a checkmark and stop to ask if anything is unexpected.

Claude verifies the install targets, adjusts the config for your provider, and runs an end-to-end smoke test. It stops and asks on anything unexpected.

---

## Method B: manual install

```bash
git clone https://github.com/Zane456/cc-session-memory.git
cd cc-session-memory
./memory_system/bin/setup.sh --global --key <your-llm-api-key>
```

setup.sh points the config at OpenAI Chat Completions (`gpt-4o-mini`) by default. To switch providers, edit the `endpoint` / `model` / `protocol` fields in `~/.config/cc-session-memory/config.json` afterwards — see the [provider matrix](#provider-matrix) below.

`setup.sh --global` does everything in one pass:

| Step | Writes to | Notes |
|---|---|---|
| Create config | `~/.config/cc-session-memory/config.json` (chmod 600) | kept if it exists; `--force` overwrites (backs up to .bak first) |
| Register Stop hook | `~/.claude/settings.json` | merged into existing hooks, nothing else touched; old file backed up to .bak |
| Install slash command | `~/.claude/commands/sess.md` | legacy entry point, coexists with the sess skill |
| Install skills | `~/.claude/skills/{sess,sessme,ccskill,ccmcp}/SKILL.md` | generated from `skills/*/SKILL.md` templates; `<CC_MEMORY_REPO>` placeholder replaced with this machine's absolute path |
| Set executable bits | hook + cli | |

Done — run `claude` in any project directory and every finished turn gets summarized in the background to `<repo>/memories/YYYY-MM-DD-<sid>.md`.

**Uninstall**: `./memory_system/bin/setup.sh --unregister-global` removes the hook (everything else in your settings stays). Skills / commands / config need manual deletion.

---

## Standardized install steps (for Claude Code — or by hand)

### 1. Check dependencies

```bash
python3 --version       # needs 3.8+
which claude            # Claude Code must be installed
uname -s                # needs Darwin or Linux (fcntl locking; Windows unsupported)
```

### 2. Run setup.sh (skip to step 3 if the user already ran it)

```bash
./memory_system/bin/setup.sh --global --key <LLM-api-key>
```

Expected output: `✓ Stop hook 写入 ...`, `✓ /sess slash 命令写入 ...`, `✓ /sess skill 写入 ...`, `✓ 全局安装完成`.

### 3. Verify the 4 install targets

```bash
test -f ~/.config/cc-session-memory/config.json && echo "✓ config"
grep -q "session_end.sh" ~/.claude/settings.json && echo "✓ hook registered"
test -f ~/.claude/skills/sess/SKILL.md && echo "✓ /sess skill installed"
test -f ~/.claude/commands/sess.md && echo "✓ /sess command installed"
```

All four `✓` must print before the install counts as done.

### 4. Smoke test (without opening Claude Code)

```bash
echo '{"session_id":"smoke-test","transcript_path":"/tmp/none","reason":"manual","last_assistant_message":"this is a smoke test message that exceeds 50 chars to ensure the worker actually fires the LLM"}' \
    | bash memory_system/hooks/session_end.sh
sleep 5
tail -20 ~/.config/cc-session-memory/logs/worker.log
```

worker.log should show `calling LLM: protocol=<openai|anthropic> model=<yours>` followed by `LLM ok in X.XXs`.

If the call fails (wrong API key, unreachable endpoint, misspelled model name, …), the error lands in `~/.config/cc-session-memory/failures/` and never pollutes memories/.

### 5. End to end

```bash
cd ~/any-project
claude
> tell me a joke                        # any single turn
> /exit
ls -lt ~/.config/cc-session-memory/logs/        # a fresh run-*.log; worker.log grew
ls -lt <repo>/memories/                 # a .md file with today's date
```

Next time you open CC in that directory:

```
> /sess                                  # load last session's summary
> /sess <keyword>                        # search a topic (current project)
> what was the exact error last time?    # sess auto-switches to --raw
> /ccskill                               # skill usage stats (all history)
> /ccmcp                                 # MCP usage stats (all history)
```

---

## Optional: auto-prune `~/.claude/projects/`

Claude Code writes the full transcript of every session to `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`, which grows without bound. (The LLM summaries don't depend on it, but `/sess --raw` does; once old ones are deleted you only get summaries.)

The repo ships `memory_system/bin/prune_cc_transcripts.py` — default 3 GB cap, protects sessions active in the last 24 hours:

```bash
# dry-run first to see what would go
python3 memory_system/bin/prune_cc_transcripts.py --dry-run

# actually delete
python3 memory_system/bin/prune_cc_transcripts.py
```

To run it daily on macOS (launchd), tell Claude Code:

> Set up a launchd plist that runs `<repo>/memory_system/bin/prune_cc_transcripts.py` daily at 22:00, labeled `com.cc-session-memory.prune-transcripts`.

Or hand-write a plist into `~/Library/LaunchAgents/`.

---

## Troubleshooting

```bash
# worker log
tail -f ~/.config/cc-session-memory/logs/worker.log

# per-invocation detach logs (by timestamp)
ls -lt ~/.config/cc-session-memory/logs/run-*.log | head

# fire the worker manually (bypassing CC)
echo '{"session_id":"manual-test","transcript_path":"/tmp/fake.jsonl","reason":"manual"}' \
    | bash ./memory_system/hooks/session_end.sh

# can't find memories? probably a cwd mismatch (project directory moved)
python3 ./memory_system/cli/ccmem.py list -n 5      # check the cwd field of recent entries
python3 ./memory_system/cli/ccmem.py find "<kw>" --all   # search across all projects
```

Full architecture rationale: [DESIGN.md](./DESIGN.md).

---

## Provider matrix

cc-session-memory talks to LLMs over the **OpenAI Chat Completions** or **Anthropic Messages** protocol — virtually every modern provider supports at least one. Pick a row and fill in `~/.config/cc-session-memory/config.json`:

| Provider | `endpoint` | `model` (example) | `protocol` |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1/chat/completions` | `gpt-4o-mini` | `openai` |
| Anthropic | `https://api.anthropic.com/v1/messages` | `claude-haiku-4-5-20251001` | `anthropic` |
| DeepSeek | `https://api.deepseek.com/v1/chat/completions` | `deepseek-chat` | `openai` |
| OpenRouter | `https://openrouter.ai/api/v1/chat/completions` | `anthropic/claude-haiku-4-5` | `openai` |
| Together | `https://api.together.xyz/v1/chat/completions` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | `openai` |
| Groq | `https://api.groq.com/openai/v1/chat/completions` | `llama-3.3-70b-versatile` | `openai` |
| Ollama (local, free) | `http://localhost:11434/v1/chat/completions` | `qwen2.5:7b` | `openai` |
| vLLM (local) | `http://localhost:8000/v1/chat/completions` | *(your deployed model id)* | `openai` |
| Z.AI GLM | `https://api.z.ai/api/anthropic/v1/messages` | `glm-5-turbo` | `anthropic` |

The `protocol` field is optional — the worker sniffs it from the endpoint URL (`/messages` or `/anthropic/` → `anthropic`, otherwise `openai`), so older configs without an explicit `protocol` keep working.

### Switching providers later? Let Claude Code do it

The lazy path:

```bash
cd /path/to/cc-session-memory
claude
```

If the new provider needs a different API key, feed it in **outside the chat** first (same rule as install):

```bash
./memory_system/bin/setup.sh --force --key <new-provider-key>   # keeps your other config fields, backs up .bak
```

Then tell Claude:

> Change my `~/.config/cc-session-memory/config.json` to deepseek — endpoint / model / protocol only, the key is already in place.

Claude Code looks up deepseek's endpoint and protocol in the table above, edits your config in place, and suggests a smoke test. Same for ollama / openai / anthropic / anything that speaks either protocol.
