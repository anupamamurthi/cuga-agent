# Newsletter New — NL-Driven Event Pipeline

A newsletter pipeline where **everything is configured through natural language**.
No YAML editing, no Python config files — just describe what you want and the
pipeline is set up, running, and delivering digests automatically.

Demonstrates the full "NL → event-driven pipeline" pattern:

```
User NL utterance
  └─► PipelineBuilder (direct LLM call)
        ├─► needs more info? → clarification question → loop
        └─► ready? → POST config to CugaHost
              └─► CugaRuntime starts:
                    RssChannel polls feeds  ─► buffer
                    CronChannel fires       ─► agent curates ─► EmailChannel delivers
```

---

## Quick start

### 1. Start the host (once — stays running, survives restarts)

```bash
# From the newsletter_new directory
cugahost start --factories newsletter_new.host_factories
```

The host is the always-on daemon. It owns all running pipelines and restores
them across restarts. Start it once; leave it running.

### 2. Set up a pipeline in plain English

```bash
# Interactive REPL
python chat.py

# Or one-liner
python chat.py "watch arxiv for AI agents, email me@example.com every morning"
```

### 3. That's it

The pipeline runs. Every morning, the agent curates new RSS items and sends a
styled HTML digest. No YAML. No Python. Just the sentence you typed.

---

## Example utterances

```
"watch arxiv cs.AI for AI agent research, email me@example.com every morning at 8am"

"monitor HuggingFace and VentureBeat for LLM news, daily digest at 6pm"

"arxiv and hacker news, keywords: agent RAG reasoning, hourly digest, log to console"

"watch https://hnrss.org/newest?q=LLM+agent for any LLM agent posts, email me@x.com daily"
```

### Management commands (in the REPL)

```
list          → show all running pipelines
status        → show host health (port, pid, runtime count)
stop          → stop the newsletter-new-digest pipeline
stop all      → stop all running pipelines
quit          → exit the REPL
```

---

## How clarification works

If your utterance is missing a required field, the builder asks one specific question:

```
You:  "watch arxiv for AI agents"
CUGA: "What schedule should the digest run on? (e.g. 'every morning at 8am', 'hourly')"
You:  "every 4 hours"
CUGA: "Would you like the digest emailed to you? If so, what's the email address?"
You:  "log to console is fine"
CUGA: "Got it! Monitoring arxiv for 'AI agents', digest every 4 hours, output to console."
      Pipeline 'newsletter-new-digest' is running.
```

The LLM extracts what it can from your message and asks exactly one question per round
until it has everything needed to create the pipeline.

---

## Provider flags

```bash
python chat.py --provider rits
python chat.py --provider anthropic
python chat.py --provider openai --model gpt-4o
```

Or set env vars before starting:

```bash
export LLM_PROVIDER=rits
export LLM_MODEL=meta-llama/llama-3-3-70b-instruct
python chat.py
```

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full component diagram and design decisions.

---

## Environment variables

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `ollama` |
| `LLM_MODEL` | Model name (e.g. `claude-sonnet-4-6`, `gpt-4o`) |
| `SMTP_USERNAME` | SMTP sender address (Gmail: your address) |
| `SMTP_PASSWORD` | SMTP app password |
| `SMTP_HOST` | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | SMTP port (default: `587`) |
| `CUGAHOST_PORT` | CugaHost port (default: `18790`) |

---

## Files

| File | Purpose |
|---|---|
| `chat.py` | CLI REPL — NL utterance → pipeline setup |
| `pipeline_builder.py` | Direct LLM call for NL → config extraction (multi-turn) |
| `channel_schemas.py` | Declarative channel schemas — source of truth for required/optional fields |
| `host_factories.py` | Registers the `newsletter_new` factory with CugaHost |
| `agent.py` | Newsletter curation agent (writes the HTML digest) |
| `skills/newsletter_curation.md` | Curation skill — how to write the newsletter |

---

## Key design decisions

**Why a direct LLM call instead of CugaAgent for pipeline building?**
Pipeline config extraction is structured JSON extraction — no tools, no reasoning
loops, no state persistence. A single LLM call is faster, simpler, and more
predictable. CugaAgent is used for the curation step (reasoning over items),
not for config extraction.

**Why does the host run separately?**
The host is the always-on daemon. It should outlive any individual chat session.
Starting a new pipeline conversation should not restart the host. `chat.py` is
a thin client that talks to the host via HTTP — same pattern as the `cugahost`
CLI itself.

**Why are channel schemas separate from PipelineBuilder?**
Channel schemas (`channel_schemas.py`) define what fields each channel type
requires or accepts. They are the source of truth. PipelineBuilder reads them
to generate its system prompt. Adding a new channel type (e.g. `webhook`, `slack`)
only requires adding one entry to `CHANNEL_SCHEMAS` — PipelineBuilder picks it up
automatically.
