"""
cuga watch — Natural-language event monitoring powered by CugaWatcher.

Usage:
    cuga watch "Watch the ACME Slack channel for mentions of 'outage' and email me at ops@acme.com"

Flow:
    NL instruction  →  WatchInstructionParser (one LLM call)  →  WatchConfig
    WatchConfig     →  WatchExecutor  →  CugaWatcher.start()
"""

from cuga.watch.models import WatchConfig, WatchSource, WatchCondition, WatchAction
from cuga.watch.parser import WatchInstructionParser
from cuga.watch.executor import WatchExecutor

__all__ = [
    "WatchConfig",
    "WatchSource",
    "WatchCondition",
    "WatchAction",
    "WatchInstructionParser",
    "WatchExecutor",
]
