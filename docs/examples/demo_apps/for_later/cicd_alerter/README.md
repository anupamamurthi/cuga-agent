# CI/CD Failure Alerter

A minimal cuga++ demo: GitHub Actions sends a webhook → `WebhookChannel` wakes the agent → agent analyses the payload → alert printed to terminal (+ emailed if SMTP is configured).

No browser UI. No scheduler. Just an HTTP listener and an agent.

---

## Architecture

```
GitHub Actions (or curl)
  └─→ POST /webhook (port 18791)
        └─→ WebhookChannel          ← cuga++ TriggerChannel
              └─→ CugaRuntime       ← cuga++ pipeline runtime
                    └─→ CugaAgent   ← cuga brain
                          └─→ skills/cicd.md  ← tells agent what to extract + format
                    └─→ smart_deliver()        ← cuga++ delivery: terminal log + optional email
```

**What each piece does:**

| Component | Role | Owned by |
|---|---|---|
| `WebhookChannel` | Listens on HTTP, fires the agent on every POST | cuga++ |
| `CugaRuntime` | Wires trigger → agent → output | cuga++ |
| `smart_deliver()` | Prints to log; emails if SMTP configured | cuga++ |
| `CugaAgent` | Analyses the JSON payload, produces the alert | cuga |
| `skills/cicd.md` | Tells the agent which fields to extract and how to format the report | app |

**What the app developer wrote:**
- `skills/cicd.md` — the formatting rules (markdown, ~30 lines)
- `main.py` — 15 lines of wiring + argparse

Everything else — the HTTP server, the async runtime, delivery routing — is cuga++.

---

## Quick start

```bash
cd docs/examples/demo_apps/cicd_alerter

# install deps (from the repo root)
pip install -e "../../../.."           # cuga
pip install -e "~/Desktop/cuga++/packages/cuga-channels[host]"
pip install -e "~/Desktop/cuga++/packages/cuga-skills"
pip install -e "~/Desktop/cuga++/packages/cuga-watcher"
```

### Run the alerter

```bash
# auto-detect LLM from env vars
python main.py

# explicit provider
python main.py --provider anthropic
python main.py --provider openai --model gpt-4o
python main.py --provider ollama          # local, no API key
python main.py --port 18792               # change port
```

You should see:
```
  CI/CD Alerter  →  listening on http://localhost:18791/webhook
  Waiting for GitHub webhook POSTs...
```

### Fire a test webhook

**Simulate a build failure:**
```bash
curl -s -X POST http://localhost:18791/webhook \
     -H 'Content-Type: application/json' \
     -d @examples/failure.json
```

**Simulate a success:**
```bash
curl -s -X POST http://localhost:18791/webhook \
     -H 'Content-Type: application/json' \
     -d @examples/success.json
```

**Send any JSON — the agent figures it out:**
```bash
curl -s -X POST http://localhost:18791/webhook \
     -H 'Content-Type: application/json' \
     -d '{"action":"completed","workflow_run":{"name":"Deploy","conclusion":"timed_out","head_branch":"main","head_sha":"abc1234","html_url":"https://github.com/org/repo/actions/runs/999","triggering_actor":{"login":"ci-bot"}},"repository":{"full_name":"org/repo"}}'
```

### Expected output (failure case)

```
🚨 Build Failed — myorg/myrepo

  Workflow : CI Tests
  Branch   : main
  Commit   : a3f8c12
  Status   : failure
  Actor    : alice
  Link     : https://github.com/myorg/myrepo/actions/runs/12345678

What likely went wrong:
  The CI Tests workflow failed on the main branch, which typically indicates
  a test assertion error or a compilation failure in a recent commit.

Suggested next step:
  Check the logs at the link above and look for failing test assertions or
  build errors in the job steps.
```

---

## Optional: email delivery

Set these env vars before running and alerts will be emailed in addition to logged:

```bash
export SMTP_USERNAME=you@gmail.com
export SMTP_PASSWORD=your-app-password   # Gmail: use an App Password
export ALERT_TO=oncall@company.com
```

No code changes needed. `smart_deliver()` detects SMTP config automatically.

---

## Optional: webhook authentication

```bash
export WEBHOOK_SECRET=mysecrettoken
python main.py
```

Requests must then include `Authorization: Bearer mysecrettoken` or they get a 401.

---

## Connecting real GitHub Actions

In your repo's GitHub settings → Webhooks → Add webhook:
- **Payload URL**: `http://your-server:18791/webhook`
- **Content type**: `application/json`
- **Events**: select `Workflow runs`
- **Secret**: set to `WEBHOOK_SECRET` if you configured one

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | auto-detected | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `litellm` \| `ollama` |
| `LLM_MODEL` | provider default | Model name override |
| `WEBHOOK_PORT` | `18791` | Port to listen on |
| `WEBHOOK_SECRET` | none | Bearer token for request auth |
| `ALERT_TO` | none | Email recipient for alerts |
| `SMTP_USERNAME` | none | SMTP sender address |
| `SMTP_PASSWORD` | none | SMTP password / app password |

---

## File structure

```
cicd_alerter/
  main.py              ← the whole app (~80 lines)
  skills/
    cicd.md            ← tells the agent how to parse + format alerts
  examples/
    failure.json       ← test payload: build failed
    success.json       ← test payload: build succeeded
  README.md
```
