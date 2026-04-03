# Newsletter Agent — Architecture

## CUGA vs cuga++

### CUGA — the reasoning engine

CUGA's job is to think.  It receives a message, reasons over it, and produces
output.  That is all.

CUGA is completely unaware of:
- Where the data came from  (RSS, database, file, webhook)
- Where the output goes     (email, Slack, log)
- When it is called         (cron schedule, polling event, manual trigger)

In the newsletter case the agent receives a message that looks like:

```
It's time to send the newsletter digest.
...
Buffered items from input channels (23 of 23):
[{"title": "...", "url": "...", "summary": "...", ...}, ...]
```

It reads the items, curates them, writes an HTML newsletter, and returns the
HTML as its answer.  Done.  No tools for sending.  No tools for fetching feeds.

### cuga++ — the infrastructure layer

cuga++ owns everything around the agent:

| Responsibility | Component |
|---|---|
| Poll data sources on a schedule | `RssChannel` (DataChannel) |
| Buffer matched items between polls | `ChannelBuffer` (internal to `CugaRuntime`) |
| Decide when to invoke the agent | `CronChannel` (TriggerChannel) |
| Assemble context and call the agent | `CugaRuntime` |
| Route agent output to delivery targets | `EmailChannel`, `LogChannel` (OutputChannel) |

The agent is never called for empty buffers.  Delivery is deterministic —
it always fires after every successful agent invocation.  The LLM never
decides whether or where to deliver.

---

## Architecture diagram

```
╔══════════════════════════════════════════════════════════════════════╗
║                          cuga++  (infrastructure)                    ║
║                                                                      ║
║   DataChannels                         TriggerChannels               ║
║  ┌─────────────────────┐              ┌──────────────────────────┐   ║
║  │ RssChannel          │              │ CronChannel              │   ║
║  │                     │              │  schedule: "0 */4 * * *" │   ║
║  │ • poll every 15 min │              │  fires every 4 hours     │   ║
║  │ • keyword filter    │              └────────────┬─────────────┘   ║
║  │ • deduplicate       │                           │ on_trigger(msg) ║
║  └──────────┬──────────┘                           │                 ║
║             │ on_data(items)                        │                 ║
║             ▼                                       ▼                 ║
║       ┌─────────────────────────────────────────────────────────┐   ║
║       │                     CugaRuntime                         │   ║
║       │                                                         │   ║
║       │  ChannelBuffer  ◄── DataChannels write here             │   ║
║       │       │                                                  │   ║
║       │       │  on trigger fire:                                │   ║
║       │       │  • read buffer                                   │   ║
║       │       │  • inject into message                           │   ║
║       │       └─────────────────────────────────────────────►   │   ║
║       └──────────────────────────┬──────────────────────────────┘   ║
║                                  │ agent.invoke(message + items)     ║
╚══════════════════════════════════╪══════════════════════════════════╝
                                   │
╔══════════════════════════════════╪══════════════════════════════════╗
║                    CUGA  (reasoning)                                 ║
║                                  ▼                                   ║
║                        ┌─────────────────┐                          ║
║                        │   CugaAgent     │                          ║
║                        │                 │                          ║
║                        │  reads items    │                          ║
║                        │  curates top N  │                          ║
║                        │  writes HTML    │                          ║
║                        │  returns output │                          ║
║                        └────────┬────────┘                          ║
║                                 │ result.answer (HTML)              ║
╚═════════════════════════════════╪════════════════════════════════════╝
                                  │
╔═════════════════════════════════╪════════════════════════════════════╗
║                    cuga++  (delivery)                                 ║
║                                  ▼                                    ║
║              CugaRuntime fans out to all OutputChannels               ║
║                                                                       ║
║   ┌───────────────────────┐    ┌──────────────────────────────────┐  ║
║   │ EmailChannel          │    │ LogChannel                       │  ║
║   │ • sends HTML email    │    │ • prints to stdout               │  ║
║   │ • SMTP, deterministic │    │ • swap in for testing            │  ║
║   └───────────────────────┘    └──────────────────────────────────┘  ║
╚═══════════════════════════════════════════════════════════════════════╝
```

---

## Package map

```
cuga++/packages/
├── cuga-channels/          channel protocol + runtime + built-in channels
│   └── src/cuga_channels/
│       ├── protocol.py     DataChannel, TriggerChannel, OutputChannel protocols
│       ├── buffer.py       ChannelBuffer — internal, deduplicates by URL
│       ├── runtime.py      CugaRuntime — orchestrates the full pipeline
│       ├── rss.py          RssChannel   (DataChannel)
│       ├── cron.py         CronChannel  (TriggerChannel)
│       ├── email.py        EmailChannel (OutputChannel)
│       └── log.py          LogChannel   (OutputChannel)
│
├── cuga-triggers/          CronTrigger, WebhookTrigger  (used by run.py)
├── cuga-watcher/           CugaWatcher pub-sub primitive (low-level)
├── cuga-skills/            CugaSkillsPlugin — injects .md skills into agent
├── cuga-checkpointer/      Conversation memory / thread persistence
└── cuga-plugin-sdk/        Plugin protocol for extending CugaAgent
```

---

## Data flow (step by step)

```
T+0        RssChannel starts. Polls all configured feeds immediately.
           Keyword filter applied. Matched items → ChannelBuffer.
           CronChannel starts. APScheduler begins counting.

T+15min    RssChannel polls again. New items deduplicated and buffered.

T+...      Buffer accumulates silently. No LLM involved.

T+4h       CronChannel fires.
           CugaRuntime reads buffer (e.g. 47 items).
           Injects items into digest message.
           Calls CugaAgent.invoke(message + items).

           CugaAgent:
             • reads items
             • curates top 10-15  (newsletter_curation skill applied)
             • writes HTML newsletter
             • returns HTML as answer

           CugaRuntime receives result.answer.
           Clears ChannelBuffer.
           EmailChannel.deliver(html)  → email sent
           LogChannel.deliver(html)   → printed to stdout

T+8h       CronChannel fires again. Cycle repeats.
```

---

## The app's only job

```python
# monitor.py — the entire application
runtime = CugaRuntime(
    agent=CugaAgent(
        model=llm,
        plugins=[CugaSkillsPlugin(skills_dir="skills/")],
        # no tools — cuga++ owns input and output
    ),
    input_channels=[
        RssChannel(sources=[...], keywords=[...], poll_minutes=15),
        CronChannel(schedule="0 */4 * * *", message="Send the digest."),
    ],
    output_channels=[
        EmailChannel.from_env(),
        LogChannel(),
    ],
)
asyncio.run(runtime.start())
```

The app declares **what** to watch, **when** to act, and **where** to deliver.
cuga++ handles the plumbing.  CUGA handles the thinking.

---

## Extending the channel system

Any class that satisfies a protocol can be dropped into `CugaRuntime`:

### Add Slack delivery

```python
output_channels=[
    EmailChannel.from_env(),
    SlackChannel(webhook_url=os.getenv("SLACK_WEBHOOK")),  # implement OutputChannel
    LogChannel(),
]
```

### Add a database source

```python
class PostgresChannel:          # implements DataChannel
    name = "postgres"
    async def start(self, on_data):
        while True:
            rows = await db.fetch("SELECT * FROM feed_items WHERE sent=false")
            if rows: await on_data(rows)
            await asyncio.sleep(300)
    async def stop(self): ...
```

### Threshold trigger (act when N items accumulate)

```python
class ThresholdChannel:         # implements TriggerChannel
    name = "threshold"
    async def start(self, on_trigger):
        while True:
            if len(self._buffer) >= self._min_items:
                await on_trigger("Threshold reached — send digest now.")
            await asyncio.sleep(60)
    async def stop(self): ...
```

---

## Files in this demo

| File | Purpose |
|---|---|
| `monitor.py` | Main entry point — builds and runs `CugaRuntime` with channels |
| `chat.py` | NL interface — utterances start monitor or one-shot |
| `run.py` | Simple scheduled mode — `CronTrigger` + `WebhookTrigger` (no channels) |
| `tools.py` | `fetch_rss`, `send_email` — used only by `run.py` and `chat.py` one-shot |
| `config.json` | Sources, keywords, schedule, LLM provider |
| `skills/newsletter_curation.md` | Editorial guide injected into agent system prompt |
