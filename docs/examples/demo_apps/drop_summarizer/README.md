# Drop Folder Summarizer

A minimal cuga++ demo using `CugaWatcher`: drop any `.txt` or `.md` file into a watched folder → agent summarizes it → entry appended to `summary_log.md` and printed to the terminal.

No HTTP, no scheduler, no channels. Just a file system poller and an agent.

---

## Architecture

```
Local filesystem
  └─→ inbox/*.txt  (or .md)
        └─→ CugaWatcher  @source(every 30s)    ← cuga++ reactive poller
              └─→ new files found?
                    └─→ @on handler fires
                          ├─→ move file to inbox/processed/  ← prevents double-processing
                          ├─→ CugaAgent.invoke(content)      ← cuga brain
                          │     └─→ skills/summarizer.md     ← formatting rules
                          └─→ append to summary_log.md
                              print to terminal
```

**What each piece does:**

| Component | Role | Owned by |
|---|---|---|
| `CugaWatcher` | Polls the folder on a schedule, emits new files as events | cuga++ |
| `@watcher.source` | Defines what to poll and how often | app (2 lines) |
| `@watcher.on` | Defines what to do when the predicate is true | app (~20 lines) |
| `CugaAgent` | Reads the document, produces the summary | cuga |
| `skills/summarizer.md` | Tells the agent the output format and edge case rules | app |

**What the app developer wrote:**
- `skills/summarizer.md` — formatting rules (~25 lines of markdown)
- `main.py` — source + handler registration (~25 lines of logic)

Everything else — the polling loop, event dispatch, cooldown, async coordination — is cuga++.

---

## How `CugaWatcher` differs from `WebhookChannel`

| | `WebhookChannel` (cicd_alerter) | `CugaWatcher` (this app) |
|---|---|---|
| Trigger | External HTTP POST | Internal polling loop |
| Data source | GitHub, Zapier, any HTTP client | Filesystem, database, API, anything |
| Use case | React to external events pushed to you | React to state changes you detect |
| Pattern | Event-driven | Polling-based reactive |

Both are part of cuga++. The cicd_alerter is push; this app is pull.

---

## Quick start

```bash
cd docs/examples/demo_apps/drop_summarizer

# install deps
pip install -e "../../../.."
pip install -e "~/Desktop/cuga++/packages/cuga-channels[host]"
pip install -e "~/Desktop/cuga++/packages/cuga-skills"
pip install -e "~/Desktop/cuga++/packages/cuga-watcher"
```

### Run the summarizer

```bash
# auto-detect LLM from env vars
python main.py

# explicit provider
python main.py --provider anthropic
python main.py --provider openai --model gpt-4o
python main.py --provider ollama        # local, no API key

# custom paths
python main.py --watch ~/Documents/notes --output ~/summaries.md

# faster polling (useful for demos)
python main.py --interval 5
```

You should see:
```
  Drop Summarizer
  Watching : /path/to/drop_summarizer/inbox
  Log      : /path/to/drop_summarizer/summary_log.md
  Interval : every 30s

  Drop any .txt or .md file into the watch folder.
```

### Drop a file

Open a second terminal and drop any text file into the inbox:

```bash
echo "Meeting notes from April 5.
Attendees: Alice, Bob, Charlie.
We decided to move the release date to April 20.
Alice will update the roadmap doc by EOW.
Bob will run the regression suite on Wednesday.
Blocked on: design sign-off from Charlie." > inbox/meeting_notes.txt
```

Within 30 seconds (or whatever `--interval` is set to) you'll see:

```
────────────────────────────────────────────────────────────
  meeting_notes.txt  (2026-04-05 14:32)
────────────────────────────────────────────────────────────
Release moved to April 20; two action items before then.

Key points:
- Alice, Bob, and Charlie attended
- Release date pushed to April 20
- Design sign-off from Charlie is a current blocker

Action items:
- Alice: update the roadmap doc by end of week
- Bob: run regression suite on Wednesday
```

The file is moved to `inbox/processed/` and a matching entry is added to `summary_log.md`.

---

## Demo ideas

Drop different kinds of files to show how the skill file handles each case:

```bash
# A brain dump / note
echo "Random idea: use embeddings to deduplicate RSS items before they hit the buffer. 
Might be overkill but could improve digest quality significantly. 
Worth a spike — maybe 2 hours." > inbox/idea.txt

# A technical spec snippet
cat > inbox/spec.txt << 'EOF'
WebhookChannel API

WebhookChannel(port=18791, path="/webhook", secret_token=None)
  Starts a FastAPI server on the given port.
  Fires on_trigger callback with the JSON body as a formatted string.
  Supports optional Bearer token auth via secret_token.
  Returns 200 {"ok": true} on success, 401 on auth failure.
EOF

# A standup
echo "Yesterday: finished the RssChannel keyword filtering PR.
Today: starting IMAPChannel integration tests.
Blockers: none." > inbox/standup.txt
```

---

## How the processed/ folder works

Files are moved to `inbox/processed/` immediately when the handler fires, before the agent is called. This means:
- A file is never summarized twice, even if the agent is slow
- Processed files are preserved — nothing is deleted
- You can always go back and check what was processed

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | auto-detected | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `litellm` \| `ollama` |
| `LLM_MODEL` | provider default | Model name override |
| `WATCH_DIR` | `./inbox` | Folder to watch |
| `OUTPUT_FILE` | `./summary_log.md` | Where summaries are appended |
| `POLL_SECONDS` | `30` | Polling interval in seconds |

---

## File structure

```
drop_summarizer/
  main.py              ← the whole app (~100 lines)
  skills/
    summarizer.md      ← tells the agent how to format summaries
  inbox/               ← created on first run — drop files here
    processed/         ← files moved here after summarization
  summary_log.md       ← appended to on each new file (created on first run)
  README.md
```
