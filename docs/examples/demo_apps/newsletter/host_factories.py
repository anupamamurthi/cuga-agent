"""
Newsletter host factories — registered with CugaHost.

  cugahost start --factories host_factories   (standalone daemon)
  host.load_factories("host_factories")        (embedded)
"""
from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent
_SKILLS_DIR  = _EXAMPLE_DIR / "skills"

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DEFAULT_SOURCES = [
    "https://arxiv.org/rss/cs.AI",
    "https://arxiv.org/rss/cs.LG",
    "https://huggingface.co/blog/feed.xml",
    "https://hnrss.org/newest?q=LLM+AI+agent&points=10",
    "https://venturebeat.com/category/ai/feed/",
]
_DEFAULT_KEYWORDS = [
    "LLM", "large language model", "agent", "agentic",
    "RAG", "reasoning", "Claude", "GPT", "Gemini", "Llama",
    "CUGA", "ALTK",
]
_DIGEST_MESSAGE = (
    "It's time to send the newsletter digest.\n\n"
    "You have been given a list of RSS items collected since the last digest.\n"
    "Select the most significant and diverse items, avoiding duplicates from prior runs.\n"
    "Compose a styled HTML newsletter using the newsletter_curation skill.\n\n"
    "Your full output will be delivered automatically — do not call any send or email tools."
)


def _make_agent(config: dict):
    from cuga import CugaAgent
    from cuga_skills import CugaSkillsPlugin
    from _llm import create_llm
    return CugaAgent(
        model=create_llm(provider=config.get("provider"), model=config.get("model")),
        plugins=[CugaSkillsPlugin(skills_dir=str(_SKILLS_DIR))],
        cuga_folder=str(_EXAMPLE_DIR / ".cuga"),
    )


def register(host) -> None:
    from cuga_channels import CronChannel, EmailChannel, RssChannel, RuntimeFactory

    host.register_factory("newsletter", RuntimeFactory.declare(
        agent_fn=_make_agent,
        message=_DIGEST_MESSAGE,
        thread_id="newsletter-digest",
        data=[lambda cfg: RssChannel.from_config(cfg, _DEFAULT_SOURCES, _DEFAULT_KEYWORDS)],
        trigger=CronChannel.from_config,
        output=EmailChannel.from_config,
        subject_prefix="CUGA Newsletter",
    ))
