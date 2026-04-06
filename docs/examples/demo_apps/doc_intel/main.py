"""
Document Intelligence Pipeline — cuga++ + docling-agent demo
==============================================================

CugaWatcher polls an inbox folder for new PDFs and images.
When files appear, a CugaAgent — equipped with docling-agent tools —
decides what kind of document it is and applies the right operation:

  - Invoice / CV / form  →  extract structured fields (JSON)
  - Report / article     →  enrich with summary, keywords, entities
  - Any PDF + question   →  RAG query against the document

Results are printed to the terminal and appended to intel_log.md.
Original files are moved to processed/ after handling.

Run:
    python main.py
    python main.py --provider anthropic
    python main.py --watch ./inbox --interval 15

Then drop any PDF or image into the inbox folder.

Environment variables:
    LLM_PROVIDER       rits | anthropic | openai | watsonx | litellm | ollama
    LLM_MODEL          model override
    DOCLING_MODEL      model id for docling agents (default: same as LLM_MODEL)
    WATCH_DIR          folder to watch (default: ./inbox)
    OUTPUT_FILE        intel log file  (default: ./intel_log.md)
    POLL_SECONDS       polling interval (default: 30)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_DIR       = Path(__file__).parent
_DEMOS_DIR = _DIR.parent

for _p in [str(_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp"}


# ---------------------------------------------------------------------------
# Docling tools — each wraps one docling-agent capability
# ---------------------------------------------------------------------------

def _make_docling_tools():
    from langchain_core.tools import tool

    @tool
    def extract_document(file_path: str, extraction_hint: str = "") -> str:
        """
        Extract structured fields from a PDF or image using docling.
        Best for: invoices, CVs, forms, receipts, contracts.

        Args:
            file_path:        Absolute path to the PDF or image file.
            extraction_hint:  Optional hint about what fields to extract,
                              e.g. "invoice fields: number, vendor, total, date"
                              or "CV fields: name, email, skills, experience"
        """
        try:
            from docling_agent.agents import DoclingExtractingAgent
            from docling.document_converter import DocumentConverter
            from mellea.backends import model_ids as mid

            # Infer schema from hint or use a generic one
            schema: dict = {"fields": extraction_hint} if extraction_hint else {
                "type": "object",
                "properties": {
                    "document_type": {"type": "string"},
                    "key_fields": {"type": "array", "items": {"type": "string"}},
                    "extracted_values": {"type": "object"},
                },
            }

            model_id = _resolve_docling_model()
            agent = DoclingExtractingAgent(model_id=model_id, tools=[])
            result_doc = agent.run(
                task=f"Extract structured data. {extraction_hint}",
                sources=[Path(file_path)],
            )
            return result_doc.export_to_markdown()
        except ImportError:
            return json.dumps({"error": "docling-agent not installed. Run: pip install docling-agent"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @tool
    def enrich_document(file_path: str) -> str:
        """
        Enrich a document with AI-generated summary, keywords, and key entities.
        Best for: research papers, reports, articles, meeting notes in PDF form.

        Args:
            file_path: Absolute path to the PDF or image file.
        """
        try:
            from docling_agent.agents import DoclingEnrichingAgent
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            docling_doc = converter.convert(file_path).document

            model_id = _resolve_docling_model()
            agent = DoclingEnrichingAgent(model_id=model_id, tools=[])
            enriched = agent.run(
                task="Summarize the document, extract search keywords, and detect key entities.",
                document=docling_doc,
            )
            return enriched.export_to_markdown()
        except ImportError:
            return json.dumps({"error": "docling-agent not installed. Run: pip install docling-agent"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    @tool
    def query_document(file_path: str, question: str) -> str:
        """
        Answer a question about a PDF document using docling RAG.
        Best for: any question about content, figures, tables, or data in a PDF.

        Args:
            file_path: Absolute path to the PDF file.
            question:  The question to answer about the document.
        """
        try:
            from docling_agent.agents import DoclingRAGAgent
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            docling_doc = converter.convert(file_path).document

            model_id = _resolve_docling_model()
            agent = DoclingRAGAgent(model_id=model_id)
            answer_doc = agent.run(task=question, document=docling_doc)
            return answer_doc.export_to_markdown()
        except ImportError:
            return json.dumps({"error": "docling-agent not installed. Run: pip install docling-agent"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return [extract_document, enrich_document, query_document]


def _resolve_docling_model():
    """Map LLM_MODEL/DOCLING_MODEL env var to a mellea model_ids entry."""
    try:
        from mellea.backends import model_ids
        name = os.getenv("DOCLING_MODEL") or os.getenv("LLM_MODEL", "")
        if "anthropic" in name.lower() or "claude" in name.lower():
            return model_ids.ANTHROPIC_CLAUDE_3_5_SONNET
        if "gpt" in name.lower() or os.getenv("LLM_PROVIDER") == "openai":
            return model_ids.OPENAI_GPT_OSS_20B
        return model_ids.OPENAI_GPT_OSS_20B   # sensible default
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def make_agent():
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm

    return CugaAgent(
        model=create_llm(
            provider=os.getenv("LLM_PROVIDER"),
            model=os.getenv("LLM_MODEL"),
        ),
        tools=_make_docling_tools(),
        plugins=[CugaSkillsPlugin(skills_dir=str(_DIR / "skills"))],
        cuga_folder=str(_DIR / ".cuga"),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(watch_dir: Path, output_file: Path, poll_seconds: int):
    from cuga_watcher import CugaWatcher

    agent = make_agent()
    watcher = CugaWatcher(agent=agent)
    processed_dir = watch_dir / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    @watcher.source(every_minutes=poll_seconds / 60, name="new_docs")
    async def check_folder() -> list[Path]:
        files = [
            f for f in watch_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if files:
            log.info("Found %d new document(s): %s", len(files), [f.name for f in files])
        return files

    @watcher.on(check_folder, when=lambda files: len(files) > 0)
    async def process_documents(files: list[Path]):
        for file_path in files:
            dest = processed_dir / file_path.name
            try:
                shutil.move(str(file_path), str(dest))
            except Exception as e:
                log.warning("Could not move %s: %s", file_path.name, e)
                continue

            log.info("Processing: %s", file_path.name)
            result = await agent.invoke(
                f"A new document has arrived: {dest}\n\n"
                f"Examine it, decide what kind of document it is, "
                f"and apply the most appropriate docling tool. "
                f"Return a structured intelligence report.",
                thread_id=f"doc-{file_path.stem}",
            )

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = (
                f"\n---\n\n"
                f"## {file_path.name} — {timestamp}\n\n"
                f"{result.answer}\n"
            )
            with open(output_file, "a", encoding="utf-8") as f:
                f.write(entry)

            print(f"\n{'═' * 64}")
            print(f"  {file_path.name}  ({timestamp})")
            print(f"{'═' * 64}")
            print(result.answer)
            print()

    print(f"\n  Document Intelligence Pipeline")
    print(f"  Watching : {watch_dir}")
    print(f"  Log      : {output_file}")
    print(f"  Interval : every {poll_seconds}s")
    print(f"  Tools    : extract_document · enrich_document · query_document")
    print(f"\n  Drop any PDF or image into the watch folder.\n")

    await watcher.start()


def main():
    parser = argparse.ArgumentParser(description="Document Intelligence Pipeline — cuga++ + docling")
    parser.add_argument("--provider",  "-p", default=None,
                        choices=["rits", "watsonx", "openai", "anthropic", "litellm", "ollama"])
    parser.add_argument("--model",     "-m", default=None)
    parser.add_argument("--watch",          default=os.getenv("WATCH_DIR",   str(_DIR / "inbox")))
    parser.add_argument("--output",         default=os.getenv("OUTPUT_FILE", str(_DIR / "intel_log.md")))
    parser.add_argument("--interval",  "-i", type=int,
                        default=int(os.getenv("POLL_SECONDS", "30")))
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model

    watch_dir   = Path(args.watch)
    output_file = Path(args.output)
    watch_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(run(watch_dir, output_file, args.interval))


if __name__ == "__main__":
    main()
