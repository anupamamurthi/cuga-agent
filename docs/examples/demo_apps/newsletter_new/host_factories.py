"""
Newsletter New — CugaHost factory registration.

Called by CugaHost when it loads this module via:
    cugahost start --factories newsletter_new.host_factories

Or embedded via:
    host.load_factories("newsletter_new.host_factories")

Registers a single factory: "newsletter_new".

The factory accepts the flat config dict produced by PipelineBuilder:
    {
        "sources":        [...],      # RSS feed URLs
        "keywords":       [...],      # filter terms
        "poll_minutes":   15,         # RSS poll frequency
        "schedule":       "0 8 * * *", # cron expression
        "email":          "...",      # recipient (optional)
        "subject_prefix": "...",      # email subject prefix
    }
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE  = Path(__file__).parent
_DEMOS = _HERE.parent

for _p in [str(_HERE), str(_DEMOS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DIGEST_MESSAGE = (
    "It's time to send the newsletter digest.\n\n"
    "You have been given a list of RSS items collected since the last digest. "
    "Select the most significant and diverse items, avoiding duplicates from prior runs. "
    "Compose a styled HTML newsletter using the newsletter_curation skill.\n\n"
    "Your full output will be delivered automatically — do not call any send or email tools."
)


def register(host) -> None:
    """Register the 'newsletter_new' factory with a CugaHost instance."""
    from cuga_channels import CronChannel, EmailChannel, LogChannel, RssChannel, RuntimeFactory

    def _make_agent(_config: dict):
        from agent import make_agent
        return make_agent()

    host.register_factory(
        "newsletter_new",
        RuntimeFactory.declare(
            agent_fn=_make_agent,
            message=_DIGEST_MESSAGE,
            thread_id="newsletter-new-digest",
            trigger=CronChannel.from_config,
            data=[RssChannel.from_config],
            output=EmailChannel.from_config,
            subject_prefix="CUGA Newsletter",
            require_buffer=True,
        ),
    )
