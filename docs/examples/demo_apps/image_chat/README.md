# Image Chat

Upload images and PDFs and have a conversation about them. A background watcher also monitors an inbox folder and automatically analyzes new files.

**Port:** 18795

## Features

- **Docling extraction** — uses `docling` to extract text and structure from PDFs, PNGs, JPEGs, TIFFs
- **Inbox watcher** — monitors `./inbox` for new image/PDF files and auto-analyzes them
- **Keyword alerts** — email notification when an analysis matches configured keywords
- **Persistent log** — all analyses stored in `analyses.db` (SQLite)
- **Browser UI** — drop zone + chat with 8 example question chips + analysis feed

## Quick Start

```bash
pip install -r requirements.txt
python main.py
# open http://127.0.0.1:18795
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
2. Upload an image or PDF via the drop zone, or drop files into `./inbox`
3. Chat with the agent about the uploaded content
4. Configure alert keywords to get emailed when specific content is detected

## Example Questions

- "What does this image show?"
- "Extract all text from this document"
- "What tables are present in this PDF?"
- "Describe the chart on this page"
- "What are the key figures in this report?"
