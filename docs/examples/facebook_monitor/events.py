"""
Event dataclasses for the event-driven Facebook monitor pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewPostEvent:
    """Emitted by the Poller when a previously-unseen post is fetched."""
    post_id: str          # stable hash of the post text, used for deduplication
    group_name: str
    group_url: str
    text: str
    fetched_at: datetime = field(default_factory=datetime.now)


@dataclass
class KeywordMatchEvent:
    """Emitted by the Matcher when a post contains one or more watched keywords."""
    post_id: str
    group_name: str
    group_url: str
    text: str
    matched_keywords: list[str]
    fetched_at: datetime = field(default_factory=datetime.now)
