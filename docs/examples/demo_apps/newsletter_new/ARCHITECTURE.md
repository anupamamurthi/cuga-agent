# Newsletter — Architecture

## Design principle

**The pipeline infrastructure owns scheduling, data fetching, and delivery.
The agent owns only the curation step.**

CugaHost manages all channel lifecycle. The agent never polls RSS feeds, never
decides when to fire, never sends email. It receives a batch of text items and
returns a formatted digest.

---

## Component map

```
┌─────────────────────────────────────────────────────────────────┐
│  Infrastructure (CugaHost daemon)                               │
│                                                                 │
│  ┌────────────┐   ┌────────────┐   ┌──────────────────────┐    │
│  │ RssChannel │   │ CronChannel│   │ CugaRouter           │    │
│  │ polls feeds│   │ fires on   │   │ (LLM classifier)     │    │
│  │ buffers    │   │ schedule   │   │ DIRECT/PIPELINE/     │    │
│  │ items      │   └─────┬──────┘   │ CONTROL              │    │
│  └─────┬──────┘         │          └──────────────────────┘    │
│        │                │                                       │
│        └────────┬────────┘                                      │
│                 ▼                                               │
│          ┌──────────────────┐                                   │
│          │   CugaAgent      │  ← newsletter_curation skill     │
│          │   (no tools)     │                                   │
│          │   curates items  │                                   │
│          │   → HTML digest  │                                   │
│          └──────┬───────────┘                                   │
│                 │                                               │
│                 ▼                                               │
│          ┌──────────────┐                                       │
│          │ EmailChannel │  → delivers digest to recipient       │
│          └──────────────┘                                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  App layer (chat.py)                                            │
│                                                                 │
│  register_app("newsletter", agent="agent:make_agent", ...)     │
│  CugaREPL loop → POST /app/newsletter/chat → CugaRouter        │
└─────────────────────────────────────────────────────────────────┘
```

---

## What the infrastructure owns

| Responsibility | Component |
|---|---|
| RSS polling and buffering | `RssChannel` |
| Schedule management | `CronChannel` |
| Email delivery | `EmailChannel` |
| NL → pipeline config extraction | `CugaRouter` (direct LLM call, not CugaAgent) |
| Pipeline lifecycle (start/stop/restore) | `CugaHost` daemon |
| DIRECT/CONTROL mode routing | `CugaRouter` |

## What CugaAgent owns

| Responsibility | How |
|---|---|
| Newsletter curation | Receives buffered RSS items, writes styled HTML digest |
| Direct answers | Receives user question, returns answer (DIRECT mode) |

---

## Agent configuration

```python
# agent.py
CugaAgent(
    model   = create_llm(...),
    tools   = [],                          # no tools
    plugins = [CugaSkillsPlugin(...)],     # newsletter_curation.md skill
)
```

The agent does not fetch RSS, does not check schedules, does not send email.
All data arrives pre-fetched in the prompt. All delivery happens in the channel
layer after the agent returns.

---

## Three routing modes

**DIRECT** — user asks a question, agent answers immediately:
```
You: "What are the key AI trends this week?"
→ CugaRouter: mode=DIRECT
→ agent.invoke(message) → answer
```

**PIPELINE** — user describes a monitoring task, infrastructure sets it up:
```
You: "watch arxiv for AI agents, email me@x.com every morning"
→ CugaRouter: mode=PIPELINE
→ config = { sources: [arxiv], schedule: "0 8 * * *", output: email }
→ CugaHost builds RssChannel + CronChannel + EmailChannel
→ "Pipeline 'arxiv-ai-daily' is now running."
```

**CONTROL** — user manages existing pipelines:
```
You: "list my pipelines"  → active pipeline list
You: "stop the arxiv pipeline"  → runtime stopped
```

---

## Why a direct LLM call for routing, not CugaAgent

`CugaRouter` uses a direct LLM call to classify and extract pipeline config.
This is structured JSON extraction — no tool calls, no reasoning loops, no
conversation history needed. A direct call is faster, cheaper, and more
predictable than routing through CugaAgent. CugaAgent is used only where
open-ended reasoning is required (curation, direct Q&A).

---

## Pipeline data flow (full)

```
1.  cugahost start
      → CugaHost daemon running on port 18790
      → restores any previously persisted pipelines

2.  python chat.py
      → register_app("newsletter", agent="agent:make_agent", skills_dir="./skills")
      → CugaREPL starts

3.  You: "watch arxiv cs.AI, email me@x.com every morning"
      → POST /app/newsletter/chat
      → CugaRouter: mode=PIPELINE, extracted config
      → CugaHost builds runtime:
            RssChannel(sources=[arxiv.org/rss/cs.AI]) — polls every 15min
            CronChannel("0 8 * * *")                  — fires at 8am
            CugaAgent + newsletter_curation skill
            EmailChannel(to="me@x.com")

4.  Every 15 min: RssChannel polls arxiv, buffers new items

5.  At 8am: CronChannel fires
      → buffered items passed to CugaAgent
      → agent curates → HTML digest
      → EmailChannel sends digest to me@x.com
```
