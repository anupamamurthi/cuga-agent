# Document Pipeline

An automated document processing pipeline. Drop PDFs and images into an inbox folder — the agent classifies each document, extracts structured data, and logs a report to SQLite. Keyword-matched documents trigger email alerts.

**Port:** 18796

## Features

- **Docling extraction** — uses `docling` to read PDFs, PNGs, JPEGs, TIFFs
- **Auto-classification** — agent identifies invoice, CV, contract, report, or meeting notes
- **Inbox watcher** — polls `./inbox` every N seconds, moves processed files to `./inbox/processed/`
- **Keyword alerts** — email notification when a document report matches configured keywords
- **Persistent log** — all reports stored in `pipeline.db` (SQLite)
- **Browser UI** — upload drop zone + settings + chat over processed documents

## Quick Start

```bash
pip install -r requirements.txt
python main.py
# open http://127.0.0.1:18796
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
2. Upload a PDF/image via the drop zone, or drop files into `./inbox`
3. The watcher picks them up, extracts text, classifies the document type, and generates a report
4. Reports appear in the document log panel; click any to see the full report
5. Configure keywords (e.g. `overdue, urgent`) to receive email alerts

## Supported Document Types

| Type | Key fields extracted |
|---|---|
| Invoice / Receipt | Number, vendor, total, currency, date, line items |
| CV / Resume | Name, email, phone, role, skills, experience |
| Contract / Agreement | Parties, effective date, obligations, payment terms |
| Report / Paper | Title, date, findings, recommendations |
| Meeting Notes | Date, attendees, decisions, action items |

## Example Questions

- "What was the last document processed?"
- "Show me all invoices from this week"
- "Were there any CVs with Python skills?"
- "Summarize the last contract that came through"
