# Drop Summarizer

Watches an inbox folder for new text, Markdown, and PDF files. Each file is automatically summarized by the agent, logged to SQLite, and optionally triggers an email alert when the summary matches configured keywords.

**Port:** 18794

## Features

- **Inbox watcher** — polls `./inbox` every N seconds for `.txt`, `.md`, `.pdf` files
- **Auto-summarize** — agent reads each file and produces a concise summary
- **Keyword alerts** — email notification when a summary matches your alert keywords
- **Persistent log** — all summaries stored in `summaries.db` (survives restarts)
- **Browser UI** — upload files via drag-and-drop or chat about your summaries
- **SMTP credentials** — configure email directly from the UI

## Quick Start

```bash
pip install -r requirements.txt
python main.py
# open http://127.0.0.1:18794
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | — | `rits` \| `anthropic` \| `openai` \| `ollama` |
| `LLM_MODEL` | — | Model override |
| `POLL_SECONDS` | 15 | Inbox poll interval |
| `SMTP_HOST` | — | e.g. `smtp.gmail.com` |
| `SMTP_USERNAME` | — | Your email address |
| `SMTP_PASSWORD` | — | App password |
| `ALERT_TO` | — | Alert recipient email |

## Usage

1. Start the server and open the browser UI
2. Drop files into `./inbox` **or** upload via the drop zone in the UI
3. The watcher picks them up, summarizes each one, and logs it
4. Configure keywords (e.g. `urgent, budget, critical`) — matching summaries trigger an email

## Example Questions

- "What were the main points in the latest file?"
- "Summarize everything uploaded today"
- "Did any documents mention deadlines?"
- "Show me all summaries from this week"
