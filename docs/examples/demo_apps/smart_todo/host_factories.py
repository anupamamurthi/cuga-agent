"""
Smart Todo host factories — registered with CugaHost.

  cugahost start --factories host_factories   (standalone daemon)
  host.load_factories("host_factories")        (embedded)
"""
from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_DIR = Path(__file__).parent
_DEMOS_DIR   = _EXAMPLE_DIR.parent

for _p in [str(_EXAMPLE_DIR), str(_DEMOS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DIGEST_MESSAGE = (
    "Good morning! Compile and send the daily todo digest.\n"
    "1. Call list_todos(status='active') to get open items.\n"
    "2. Call list_todos(status='done') to get items completed since yesterday.\n"
    "Compose a single styled HTML email with two sections: "
    "'✅ Completed' (done items) and '📋 Still open' (active items, grouped by priority).\n"
    "Return the HTML — do not call any send or email tools."
)


def register(host) -> None:
    from cuga_channels import CronChannel, EmailChannel, RuntimeFactory
    from agent import get_agent

    host.register_factory("digest", RuntimeFactory.declare(
        agent_fn=lambda _: get_agent(),
        message=_DIGEST_MESSAGE,
        thread_id="smart-todo-digest",
        trigger=CronChannel.from_config,
        output=EmailChannel.from_config,
        subject_prefix="📋 Smart Todo Digest",
        require_buffer=False,
    ))
