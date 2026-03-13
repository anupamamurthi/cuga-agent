# `cuga watch` — Use Cases & Enterprise Patterns

A reference for what to watch, when to trigger, what to do — and when an agent
genuinely earns its place over plain Python.

---

## The core question: Python or Agent?

```
Is your condition exact-matchable (keyword / regex)?
  YES → plain Python is fine. No agent needed.
  NO  → you need semantic understanding → use agent_notify

Is your action a fixed template (send same email every time)?
  YES → email / sms / log actions. No agent needed.
  NO  → judgment, synthesis, or multi-step routing → use agent_notify

Do you need to reason across multiple events over time?
  NO  → stateless Python handler is fine.
  YES → thread_id + agent memory → use agent_notify

Does the source produce unstructured text (chat, docs, transcripts)?
  NO  → structured API, parse it in Python.
  YES → Slack, email, meeting transcripts, Box docs → agent extracts meaning
```

The agent earns its place when: **unstructured input + judgment required + multi-step output**.
Everything else: write Python.

---

## Sources

### Communication & Collaboration

| Source | What to watch | Works today |
|--------|--------------|-------------|
| Facebook group | Community posts, recommendations, requests | Yes (Playwright) |
| RSS / Atom feed | Any blog, news, release feed, podcast | Yes |
| Web page | Any public URL — status pages, career pages, docs | Yes |
| Slack channel | #incidents, #sales-wins, customer DMs | Needs adapter |
| MS Teams channel | Engineering standups, exec updates, deal rooms | Needs adapter |
| Teams meeting transcript | Action items, decisions, keywords spoken in calls | Needs adapter |
| Email inbox (IMAP) | Vendor invoices, escalations, specific senders | Needs adapter |
| Discord server | Community feedback, bug reports | Needs adapter |

### Documents & Files

| Source | What to watch | Works today |
|--------|--------------|-------------|
| Box folder | New uploads, edits, permission changes | Needs adapter |
| SharePoint / OneDrive | Document changes, new uploads in a project folder | Needs adapter |
| Google Drive | New sheets, doc edits by specific people | Needs adapter |
| Confluence space | Page edits, new comments | Needs adapter |
| Notion database | New rows, status changes, property updates | Needs adapter |

### Engineering & DevOps

| Source | What to watch | Works today |
|--------|--------------|-------------|
| GitHub releases (Atom) | New releases, tags | Yes (RSS) |
| GitHub Issues (Atom) | New issues, label changes | Yes (RSS) |
| GitHub Actions | Build failures, deploy completions | Needs adapter |
| PagerDuty / OpsGenie | New incidents, escalations | Needs adapter |
| Datadog / Grafana | Metric thresholds, anomaly alerts | Needs adapter |
| Jira board | Ticket status changes, new P1s, sprint completion | Needs adapter |
| Sentry | New error groups, spike in error rate | Needs adapter |

### Business & CRM

| Source | What to watch | Works today |
|--------|--------------|-------------|
| Hacker News RSS | Brand mentions, competitor mentions, tech trends | Yes |
| TechCrunch / Bloomberg RSS | Industry news, competitor press | Yes |
| SEC EDGAR RSS | Competitor filings, 8-K / 10-K releases | Yes |
| LinkedIn (career pages) | Competitor job postings, hiring signals | Yes (web_page) |
| G2 / Trustpilot | New reviews, rating drops | Yes (web_page) |
| Salesforce | Deal stage changes, new leads, churn signals | Needs adapter |
| Zendesk / Intercom | New tickets, SLA breaches, CSAT drops | Needs adapter |

### Finance & Compliance

| Source | What to watch | Works today |
|--------|--------------|-------------|
| FTC / ICO RSS | Regulatory announcements, enforcement actions | Yes |
| Regulations.gov RSS | Policy changes, comment periods | Yes |
| SAP / NetSuite | Invoice approvals, budget overruns | Needs adapter |
| Workday | New hires, org changes, headcount updates | Needs adapter |

---

## Conditions

| Type | Logic | When to use |
|------|-------|-------------|
| `keyword` | Any keyword in text (case-insensitive) | Exact terms you know in advance |
| `always` | Triggers on every non-empty poll | Release feeds, "notify me of everything" |
| `custom` | Python eval expression against `items` | Simple numeric or structural checks |
| `regex` | Pattern match | Jira IDs, dollar amounts, email addresses *(needs implementation)* |
| `sentiment` | LLM-scored negative / positive / neutral | Customer complaints, social monitoring *(agent_notify)* |
| `semantic` | Embedding similarity — concept-level match | "Anything about a data breach" *(agent_notify)* |
| `threshold` | Numeric field crosses a value | Queue depth, error rate, spend *(needs implementation)* |
| `new_item` | Item not seen in previous poll (dedup) | Any feed without dedup *(needs implementation)* |
| `rate` | N matches within a time window | "3 P1s in 1 hour" *(needs implementation + thread_id)* |
| `LLM judge` | Free-form: agent decides if item is relevant | Nuanced filtering — urgency, tone, intent *(agent_notify)* |

---

## Actions

| Type | What it does | Works today |
|------|-------------|-------------|
| `log` | Print match to stdout | Yes |
| `email` | SMTP email (Gmail etc.) | Yes |
| `sms` | SMS via Twilio | Yes |
| `agent_notify` | LLM composes message, can call any tool | Yes |
| Slack message | Post to channel, DM, thread reply | Needs adapter |
| Teams message | Post to channel or adaptive card | Needs adapter |
| PagerDuty alert | Open incident, set severity | Needs adapter |
| Jira ticket | Create issue with labels, assignee, priority | Needs adapter |
| GitHub issue | Open issue, add label, assign | Needs adapter |
| Webhook POST | Hit any internal endpoint (Zapier, n8n, custom) | Needs adapter |
| Salesforce update | Update deal stage, log activity | Needs adapter |
| Calendar event | Book a meeting in response to a trigger | Needs adapter |
| Summarise & store | LLM writes summary → Notion/Confluence | Via agent_notify |
| Human approval | Pause, ask a human, then continue (HITL) | Via agent policies |

---

## Worked Examples

### 1. Facebook group → email (personal)
**Condition complexity: low | Action complexity: low | Agent: not needed**

```
"Watch https://www.facebook.com/groups/603241227268048
 for nanny or child care or sitter
 and email anupama.murthi@gmail.com"
```

```json
{
  "description": "Watch Chappaqua Moms for nanny posts",
  "sources": [{
    "type": "facebook_group",
    "url": "https://www.facebook.com/groups/603241227268048",
    "name": "Chappaqua Moms",
    "interval_minutes": 30,
    "extra": { "session_file": "./fb_session.json" }
  }],
  "condition": {
    "type": "keyword",
    "keywords": ["nanny", "child care", "sitter", "babysitter", "au pair"]
  },
  "actions": [{
    "type": "email",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "email_to": "anupama.murthi@gmail.com"
  }],
  "archive_enabled": true,
  "archive_file": "watch_archive.jsonl",
  "archive_interval_minutes": 2
}
```

**What Python does:** keyword match → fixed email template. Correct tool for this job.
**Where agent would help:** if you want it to distinguish *"looking for a nanny"* vs *"recommending their old nanny"* — the former is actionable, the latter isn't.

---

### 2. GitHub releases → engineering impact assessment
**Condition complexity: low | Action complexity: high | Agent: earns its place in the action**

```
"Watch https://github.com/langchain-ai/langchain/releases.atom and
 https://github.com/openai/openai-python/releases.atom for any new release
 and email engineering@company.com with a summary of what changed and
 whether we need to update our dependencies"
```

```json
{
  "description": "Dependency release monitoring — breaking change assessment",
  "sources": [
    {
      "type": "rss_feed",
      "url": "https://github.com/langchain-ai/langchain/releases.atom",
      "name": "LangChain Releases",
      "interval_minutes": 60
    },
    {
      "type": "rss_feed",
      "url": "https://github.com/openai/openai-python/releases.atom",
      "name": "OpenAI SDK Releases",
      "interval_minutes": 60
    }
  ],
  "condition": { "type": "always" },
  "actions": [{ "type": "agent_notify" }],
  "archive_enabled": true,
  "archive_file": "releases_archive.jsonl",
  "archive_interval_minutes": 5
}
```

**Python sends:** "LangChain 0.3.1 released."
**Agent sends:** "LangChain 0.3.1 removes `LLMChain`, which is imported in 3 of our files (`executor.py`, `parser.py`, `sdk.py`). This is a breaking change. Recommend pinning to 0.3.0 until we migrate."

---

### 3. Hacker News brand monitoring → negative sentiment only
**Condition complexity: high (sentiment) | Action complexity: low | Agent: earns its place in the condition**

```
"Watch https://news.ycombinator.com/rss every 15 minutes for mentions of
 'CUGA' or 'cuga-agent' — only alert me if the sentiment is negative or
 there's a bug report, not for general positive mentions.
 Email founders@company.com"
```

```json
{
  "description": "HN brand monitoring — negative sentiment only",
  "sources": [{
    "type": "rss_feed",
    "url": "https://news.ycombinator.com/rss",
    "name": "Hacker News",
    "interval_minutes": 15,
    "extra": { "limit": 30 }
  }],
  "condition": {
    "type": "keyword",
    "keywords": ["cuga", "cuga-agent"]
  },
  "actions": [{ "type": "agent_notify" }],
  "archive_enabled": true,
  "archive_file": "hn_mentions.jsonl",
  "archive_interval_minutes": 5
}
```

**The problem with keyword-only:** catches *"CUGA looks amazing!"* and *"CUGA crashes on Python 3.12, completely unusable"* identically. Both contain the keyword. Only the second one warrants waking you up at 2am.
**Agent:** reads the post, classifies sentiment and intent, silently drops the praise, pages you for the bug report.

---

### 4. Regulatory feed → legal team briefing
**Condition complexity: low | Action complexity: high (synthesis) | Agent: earns its place in the action**

```
"Watch https://www.ftc.gov/feeds/press-releases.xml and
 https://www.ico.org.uk/about-the-ico/media-centre/news-and-blogs/rss/
 every 6 hours for GDPR, AI regulation, data privacy, or large language model.
 When found, summarise the key obligation for a B2B SaaS company and
 email legal@company.com and cto@company.com"
```

```json
{
  "description": "Regulatory monitoring — AI and data privacy",
  "sources": [
    {
      "type": "rss_feed",
      "url": "https://www.ftc.gov/feeds/press-releases.xml",
      "name": "FTC Press Releases",
      "interval_minutes": 360
    },
    {
      "type": "rss_feed",
      "url": "https://www.ico.org.uk/about-the-ico/media-centre/news-and-blogs/rss/",
      "name": "ICO UK",
      "interval_minutes": 360
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["gdpr", "ai regulation", "data privacy", "large language model", "llm"]
  },
  "actions": [
    {
      "type": "email",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "email_to": "legal@company.com"
    },
    { "type": "agent_notify" }
  ],
  "archive_enabled": true,
  "archive_file": "regulatory_archive.jsonl",
  "archive_interval_minutes": 10
}
```

**Python sends:** raw 40-page ruling excerpt to legal@.
**Agent sends:** "FTC issued guidance on AI transparency requirements (March 2026). Key obligation for B2B SaaS: model output must be disclosed as AI-generated when used in customer-facing decisions. Recommended action: audit our report generation feature. Full ruling linked below."

---

### 5. Competitor hiring → roadmap intelligence (cross-event reasoning)
**Condition complexity: low | Action complexity: very high (pattern across polls) | Agent: thread_id is the entire value**

```
"Watch https://openai.com/careers and https://www.anthropic.com/careers
 every 4 hours for ML Engineer, Research Scientist, inference, or post-training.
 Email product@company.com with a weekly pattern analysis —
 are they scaling a specific team? What does it signal about their roadmap?"
```

```json
{
  "description": "Competitor hiring signals — roadmap intelligence",
  "sources": [
    {
      "type": "web_page",
      "url": "https://openai.com/careers",
      "name": "OpenAI Careers",
      "interval_minutes": 240
    },
    {
      "type": "web_page",
      "url": "https://www.anthropic.com/careers",
      "name": "Anthropic Careers",
      "interval_minutes": 240
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["ml engineer", "research scientist", "inference", "post-training", "rlhf", "alignment"]
  },
  "actions": [{ "type": "agent_notify" }],
  "archive_enabled": true,
  "archive_file": "hiring_archive.jsonl",
  "archive_interval_minutes": 10
}
```

**Python sees:** three unrelated scrapes over 12 hours.
**Agent on same thread_id sees:**
- Poll 1: 3 inference roles at Anthropic
- Poll 2: 5 more inference roles + "Head of Inference"
- Poll 3: 2 similar roles at OpenAI

**Agent synthesises:** "Anthropic posted 9 inference-related roles in 12 hours including a leadership hire — this is a deliberate scaling push, likely ahead of a model launch. OpenAI posted 2 similar roles. Recommend accelerating our own inference roadmap discussion."

This is the clearest Level 2 `thread_id` use case. Cross-poll pattern recognition is impossible without stateful memory.

---

### 6. Support portal → tiered escalation routing
**Condition complexity: medium | Action complexity: high (branching) | Agent: routing decision tree**

```
"Watch https://support.company.com/recent-tickets every 5 minutes
 for data loss, security breach, or can't access.
 If the ticket mentions an enterprise customer or Fortune 500,
 page on-call immediately via PagerDuty.
 Otherwise create a Jira ticket and notify #customer-success on Slack."
```

```json
{
  "description": "Support escalation — tiered by customer and issue type",
  "sources": [{
    "type": "web_page",
    "url": "https://support.company.com/recent-tickets",
    "name": "Support Portal",
    "interval_minutes": 5
  }],
  "condition": {
    "type": "keyword",
    "keywords": ["data loss", "security breach", "can't access", "cannot access", "locked out"]
  },
  "actions": [{ "type": "agent_notify" }],
  "archive_enabled": true,
  "archive_file": "support_archive.jsonl",
  "archive_interval_minutes": 2
}
```

**Python route:** `if "enterprise" in text: send_email(...)` — a decision tree that grows forever and is wrong constantly.
**Agent route:** reads the ticket, identifies customer tier from context, assesses whether this is a real data loss claim or a UX confusion, routes appropriately, and drafts a professional incident summary for whoever gets paged — not raw ticket text.

---

### 7. Exec news briefing → synthesised morning digest
**Condition complexity: low | Action complexity: very high (multi-source synthesis) | Agent: synthesis is the entire product**

```
"Watch https://techcrunch.com/feed/ and https://feeds.bloomberg.com/technology/news.rss
 every 30 minutes for Salesforce, SAP, Workday, or ServiceNow.
 Every morning compile everything from the last 24 hours into a 3-bullet briefing
 and email ceo@company.com — frame each item as whether it affects our
 upcoming partner or customer calls"
```

```json
{
  "description": "Morning exec briefing — partner and competitor news",
  "sources": [
    {
      "type": "rss_feed",
      "url": "https://techcrunch.com/feed/",
      "name": "TechCrunch",
      "interval_minutes": 30,
      "extra": { "limit": 20 }
    },
    {
      "type": "rss_feed",
      "url": "https://feeds.bloomberg.com/technology/news.rss",
      "name": "Bloomberg Tech",
      "interval_minutes": 30,
      "extra": { "limit": 20 }
    }
  ],
  "condition": {
    "type": "keyword",
    "keywords": ["salesforce", "sap", "workday", "servicenow"]
  },
  "actions": [{ "type": "agent_notify" }],
  "archive_enabled": true,
  "archive_file": "exec_briefing_archive.jsonl",
  "archive_interval_minutes": 30
}
```

**Python delivers:** 6 emails over 24 hours with raw excerpts. The CEO reads none of them.
**Agent delivers (once, at 8am via thread_id memory):**

> **Morning Brief — March 10**
> - **Salesforce** announced Einstein GPT pricing changes — affects your 2pm call with their partnership team. Suggested talking point: how this positions us vs their native AI.
> - **Workday** acquired an HR analytics startup — your 4pm prospect uses Workday; they'll likely ask about integration.
> - **SAP** had no material news in the last 24h.

---

## Enterprise use case matrix

```
USE CASE                      SOURCE          CONDITION        ACTION           AGENT?
──────────────────────────────────────────────────────────────────────────────────────────
Childcare FB group            facebook_group  keyword          email            No
Competitor release watch      rss_feed        always           agent_notify     Yes – action
HN brand sentiment            rss_feed        keyword          agent_notify     Yes – condition
Regulatory briefing           rss_feed        keyword          agent_notify     Yes – action
Competitor hiring signals     web_page        keyword          agent_notify     Yes – cross-event
Support ticket escalation     web_page        keyword          agent_notify     Yes – routing
Morning exec digest           rss_feed        keyword          agent_notify     Yes – synthesis
GitHub PR staleness           rss_feed        always           agent_notify     Yes – action
Meeting → action items        (Box/Teams)     always           agent_notify     Yes – extraction
Zendesk churn risk            (Zendesk API)   semantic/llm     agent_notify     Yes – condition
Incident pattern (3× in 1h)   (PD API)        rate             agent_notify     Yes – cross-event
Budget overrun alert          (NetSuite API)  threshold        email            No
```

---

## What's missing for full enterprise coverage

| Gap | Impact | Implementation |
|-----|--------|----------------|
| **Dedup** | Same post re-triggers every poll cycle | Hash seen items; store in `archive_buffer`, skip on next poll |
| **Webhook inbound source** | Enterprise systems push events (Zendesk, Salesforce, PD) — polling is wrong | Add `webhook_receiver` source type: FastAPI endpoint → asyncio queue |
| **Slack / Teams source** | Most enterprise signal lives in chat | Slack Events API or Teams webhook → source adapter |
| **Slack / Teams action** | Most enterprise notifications go to chat | `slack_message` and `teams_message` action types |
| **Jira / GitHub action** | Ticket creation is the most requested enterprise action | `jira_ticket`, `github_issue` action types |
| **Numeric / threshold condition** | Metric monitors need `value > N`, not keywords | `threshold` condition type + numeric field extraction |
| **Rate condition** | "3 P1s in 1 hour" needs state across polls | Ring buffer per source + count within window |
| **Auth layer** | Slack/Teams/Box all need OAuth — no abstraction today | `WatchSource.extra` carries tokens; adapters handle per-service auth |

The two highest-value additions for an enterprise deployment: **dedup** (stops alert fatigue on day one) and **webhook inbound source** (most enterprise tools push, not pull).
