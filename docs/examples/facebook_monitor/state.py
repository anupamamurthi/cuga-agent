"""
Persistent deduplication state — tracks which post IDs have already been seen
so the monitor never processes or notifies on the same post twice.

State is stored in a local JSON file (seen_posts.json) and survives restarts.
"""

import json
import hashlib
from pathlib import Path

STATE_FILE = Path(__file__).parent / "seen_posts.json"
# Cap the seen-post set so the file doesn't grow forever
MAX_SEEN = 5000


def _load() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def _save(seen: set[str]) -> None:
    # Keep only the most recent MAX_SEEN entries (sets are unordered, so we
    # just trim arbitrarily — good enough for deduplication purposes)
    trimmed = list(seen)[-MAX_SEEN:]
    STATE_FILE.write_text(json.dumps(trimmed))


def make_post_id(group_url: str, text: str) -> str:
    """Stable ID for a post: SHA-1 of group URL + first 300 chars of text."""
    payload = group_url + text[:300]
    return hashlib.sha1(payload.encode()).hexdigest()


class SeenPostsStore:
    """In-memory seen-post set backed by a JSON file."""

    def __init__(self):
        self._seen: set[str] = _load()

    def is_new(self, post_id: str) -> bool:
        return post_id not in self._seen

    def mark_seen(self, post_id: str) -> None:
        self._seen.add(post_id)
        _save(self._seen)

    def __len__(self) -> int:
        return len(self._seen)
