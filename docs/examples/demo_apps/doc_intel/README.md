# Document Intelligence Pipeline

A multimodal cuga++ demo that combines `CugaWatcher` (folder polling) with
[docling-agent](https://github.com/docling-project/docling-agent) (document AI) as tools inside a `CugaAgent`.

Drop any PDF or image into the inbox. The agent classifies it, picks the right docling
operation, and produces a structured intelligence report — printed to terminal and logged.

---

## What makes this different

The other demos use cuga++ infrastructure to move data around. This one shows something
different: **docling-agent as a `@tool` inside `CugaAgent`**.

The CugaAgent doesn't do the document parsing itself — it decides *which* docling capability
to invoke based on the filename and content, then synthesises the output into a readable report.
That's the multi-tool routing pattern: the agent as an orchestrator, specialist tools as workers.

---

## Architecture

```
inbox/                      ← drop PDFs or images here
  └─→ CugaWatcher           ← cuga++ reactive poller (every 30s)
        └─→ new files?
              └─→ move to processed/
                  └─→ CugaAgent.invoke(file_path)    ← cuga brain
                        └─→ skills/doc_intel.md       ← routing + format rules
                              ├─→ extract_document()  ← docling ExtractingAgent
                              ├─→ enrich_document()   ← docling EnrichingAgent
                              └─→ query_document()    ← docling RAGAgent
                        └─→ structured report
                              ├─→ printed to terminal
                              └─→ appended to intel_log.md
```

### The three docling tools

| Tool | docling Agent | Best for |
|---|---|---|
| `extract_document(file, hint)` | `DoclingExtractingAgent` | Invoices, CVs, contracts, forms — structured field extraction |
| `enrich_document(file)` | `DoclingEnrichingAgent` | Reports, papers, articles — summary + keywords + entities |
| `query_document(file, question)` | `DoclingRAGAgent` | Any question about a document's content |

### Who owns what

| Concern | Owned by |
|---|---|
| Polling the folder on a schedule | cuga++ (`CugaWatcher`) |
| Deciding which tool to call | cuga (`CugaAgent` + skill file) |
| Document parsing and AI extraction | docling-agent |
| Formatting the final report | cuga (`CugaAgent` + skill file) |
| Writing to `intel_log.md` | app (~5 lines) |

**What the app developer wrote:**
- `skills/doc_intel.md` — routing rules and report format (~50 lines)
- `_make_docling_tools()` — three `@tool` wrappers over docling (~60 lines)
- `run()` watcher loop — source + handler (~20 lines)

---

## Quick start

### 1. Install dependencies

```bash
cd docs/examples/demo_apps/doc_intel

pip install -e "../../../.."
pip install -e "~/Desktop/cuga++/packages/cuga-channels[host]"
pip install -e "~/Desktop/cuga++/packages/cuga-skills"
pip install -e "~/Desktop/cuga++/packages/cuga-watcher"

# Install docling-agent (document AI layer)
pip install docling-agent
# docling itself (the PDF parser)
pip install docling
```

### 2. Run

```bash
# auto-detect LLM from env vars
python main.py

# explicit provider
python main.py --provider anthropic
python main.py --provider openai --model gpt-4o
python main.py --provider ollama       # local, no API key

# custom paths or faster polling for demos
python main.py --watch ~/Downloads/docs --interval 10
```

You should see:
```
  Document Intelligence Pipeline
  Watching : .../doc_intel/inbox
  Log      : .../doc_intel/intel_log.md
  Interval : every 30s
  Tools    : extract_document · enrich_document · query_document

  Drop any PDF or image into the watch folder.
```

---

## Try it

### Invoice / receipt

Drop any invoice PDF into `inbox/`. The agent will detect it from the filename and call
`extract_document` with invoice-specific fields:

```
═══════════════════════════════════════════════════════════════
  invoice_march.pdf  (2026-04-05 14:22)
═══════════════════════════════════════════════════════════════
Document: invoice_march.pdf
Type    : Invoice
Processed: 2026-04-05 14:22

### Summary
A March 2026 invoice from Acme Supplies to CUGA Labs for cloud
infrastructure services, totalling $4,200 USD, due April 15.

### Extracted Data
- Invoice number : INV-2026-0312
- Vendor         : Acme Supplies Inc.
- Bill to        : CUGA Labs
- Date           : 2026-03-31
- Due date       : 2026-04-15
- Total          : $4,200.00 USD
- Line items     : Cloud compute (x3), Storage (1TB), Support plan

### Key Takeaways
- Payment of $4,200 is due April 15
- Three services billed: compute, storage, support
- No discounts or credits applied
```

### Research paper (PDF)

Drop a paper PDF (e.g. from arXiv). The agent will call `enrich_document`:

```
═══════════════════════════════════════════════════════════════
  attention_is_all_you_need.pdf  (2026-04-05 14:25)
═══════════════════════════════════════════════════════════════
Document: attention_is_all_you_need.pdf
Type    : Research Paper
Processed: 2026-04-05 14:25

### Summary
Introduces the Transformer architecture, which replaces recurrent
networks with a self-attention mechanism, achieving state-of-the-art
results on translation tasks with greater parallelism.

### Extracted Data
Keywords : Transformer, self-attention, encoder-decoder, multi-head
           attention, positional encoding, BLEU score, neural MT
Entities : Vaswani et al., Google Brain, WMT 2014, BLEU 28.4

### Key Takeaways
- Proposes attention-only architecture, no recurrence or convolution
- Achieves 28.4 BLEU on EN-DE translation, outperforming prior SOTA
- Significantly faster to train due to full parallelism
```

### CV / resume

Drop a CV PDF. The agent will call `extract_document` with CV-specific fields:

```
Document: john_doe_cv.pdf
Type    : CV / Resume

### Extracted Data
- Name            : John Doe
- Email           : john@example.com
- Current role    : Senior Software Engineer
- Years exp.      : 8
- Skills          : Python, Go, Kubernetes, LangChain, LLMs
- Education       : MSc Computer Science, TU Berlin (2017)
- Languages       : English (native), German (B2)
```

---

## How the skill file drives routing

The agent never hard-codes "if filename contains invoice". Instead, `skills/doc_intel.md`
gives it a routing table — a markdown table mapping filename clues to document types and tools.

This means:
- Adding a new document type (e.g. patent, prescription) = edit the markdown table
- Changing the report format = edit the markdown template
- No Python changes required

---

## Docling-agent note

This project is early-stage (`v0.1.0`, marked work-in-progress by the authors). The tool
wrappers in `main.py` will handle import errors gracefully — if docling-agent isn't installed,
the agent still runs but reports what it would have extracted instead of doing the actual
extraction.

To test without installing docling:

```bash
python main.py --provider anthropic
# Drop a file — the agent will acknowledge the document and describe
# what it would extract, but skip the actual docling call.
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | auto-detected | `rits` \| `anthropic` \| `openai` \| `watsonx` \| `litellm` \| `ollama` |
| `LLM_MODEL` | provider default | Model for CugaAgent |
| `DOCLING_MODEL` | same as `LLM_MODEL` | Model passed to docling-agent (uses mellea model_ids) |
| `WATCH_DIR` | `./inbox` | Folder to watch |
| `OUTPUT_FILE` | `./intel_log.md` | Where reports are appended |
| `POLL_SECONDS` | `30` | Polling interval in seconds |

---

## File structure

```
doc_intel/
  main.py              ← the whole app (~170 lines)
  skills/
    doc_intel.md       ← routing rules and report format
  inbox/               ← drop PDFs/images here (created on first run)
    processed/         ← files moved here after processing
  intel_log.md         ← all reports appended here (created on first run)
  README.md
```
