# Document Intelligence

An intelligent document corpus explorer. Drop PDFs and images into an inbox — they get extracted with docling and indexed into ChromaDB. Chat with the agent to search, summarize, and query across your entire document collection.

**Port:** 18797

## Features

- **ChromaDB RAG** — documents are chunked and indexed for semantic search across the full corpus
- **Docling extraction** — structured field extraction and AI enrichment for PDFs/images
- **Three tools**:
  - `extract_document` — structured fields (invoices, CVs, contracts)
  - `enrich_document` — AI summary + keywords (reports, articles)
  - `search_corpus` — semantic search over all indexed documents
- **Inbox watcher** — polls `./inbox` every N seconds, indexes new documents automatically
- **Keyword alerts** — email notification when a document matches configured keywords
- **Persistent storage** — document log in `intel.db` (SQLite) + ChromaDB index in `.chroma/`
- **Browser UI** — corpus stats bar + upload drop zone + chat with 10 example chips + document log

## Quick Start

```bash
pip install -r requirements.txt
python main.py
# open http://127.0.0.1:18797
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
2. Upload PDFs/images via the drop zone, or drop files into `./inbox`
3. The watcher extracts text, indexes chunks into ChromaDB, and logs a report
4. The stats bar shows total documents, indexed count, and ChromaDB chunk count
5. Chat with the agent to search across the corpus or ask questions about specific documents
6. Click any document card to see its full extracted report

## Supported Document Types

| Type | Key fields extracted |
|---|---|
| Invoice / Receipt | Number, vendor, total, currency, date, line items |
| CV / Resume | Name, email, phone, role, skills, experience |
| Contract / Agreement | Parties, effective date, obligations, payment terms |
| Report / Paper | Title, date, findings, recommendations |
| Meeting Notes | Date, attendees, decisions, action items |

## Example Questions

- "What documents are in my corpus?"
- "Search for budget figures across all documents"
- "Find any action items or deadlines"
- "Extract all vendor names from invoices"
- "Find mentions of payment terms"
- "What skills appear in the CVs?"
- "Any documents mentioning compliance?"
- "Summarize all reports in the corpus"
