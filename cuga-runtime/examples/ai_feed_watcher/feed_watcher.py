"""
AI feed watcher — polls Twitter/X for new posts from watched accounts.

Each new post becomes a CugaTaskEvent. CUGA decides if it's relevant to agentic AI
and produces a structured assessment. High-signal posts trigger a notification.

Usage
-----
# Poll once and exit:
    python -m examples.ai_feed_watcher.feed_watcher --runtime-url http://localhost:8001

# Continuous polling every 60s:
    python -m examples.ai_feed_watcher.feed_watcher --runtime-url http://localhost:8001 --poll-interval 60

Requires TWITTER_BEARER_TOKEN environment variable.
"""

import asyncio
import argparse
import os
import time
import logging

import httpx

from examples.ai_feed_watcher.watched_accounts import (
    WATCHED_HANDLES,
    AI_RELEVANCE_PROMPT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TWITTER_API_BASE = "https://api.twitter.com/2"


# ── Event submission ──────────────────────────────────────────────────────────

async def submit_post(
    client: httpx.AsyncClient,
    runtime_url: str,
    handle: str,
    post_id: str,
    text: str,
    created_at: str = "",
) -> dict:
    """Submit one post as a CugaTaskEvent. Returns { task_id, status }."""
    event = {
        "query": AI_RELEVANCE_PROMPT,
        "persona": "default",
        "trigger": f"twitter.new_post.{handle}",
        "context": {
            "handle": f"@{handle}",
            "post_id": post_id,
            "text": text,
            "posted_at": created_at,
        },
    }
    resp = await client.post(f"{runtime_url}/events", json=event, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


async def poll_result(
    client: httpx.AsyncClient,
    runtime_url: str,
    task_id: str,
    timeout: float = 120.0,
) -> dict:
    """Poll /events/{task_id} until completed or failed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get(f"{runtime_url}/events/{task_id}", timeout=10.0)
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(1.5)
    raise TimeoutError(f"Task {task_id} did not finish in {timeout}s")


def parse_cuga_assessment(output: str) -> dict:
    result = {"relevant": "no", "signal": None, "summary": None, "reason": None}
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("relevant:"):
            result["relevant"] = line.split(":", 1)[1].strip().lower()
        elif line.startswith("signal:"):
            result["signal"] = line.split(":", 1)[1].strip().lower()
        elif line.startswith("summary:"):
            result["summary"] = line.split(":", 1)[1].strip()
        elif line.startswith("reason:"):
            result["reason"] = line.split(":", 1)[1].strip()
    return result


def notify(handle: str, text: str, assessment: dict) -> None:
    signal = assessment.get("signal", "")
    icon = "🔴" if signal == "high" else "🟡"
    print(f"\n{icon}  [{signal.upper()} SIGNAL] @{handle}")
    print(f"   Post:    {text[:120]}{'...' if len(text) > 120 else ''}")
    print(f"   Summary: {assessment['summary']}")
    print()


# ── Twitter API ───────────────────────────────────────────────────────────────

async def fetch_recent_tweets(
    http: httpx.AsyncClient,
    user_id: str,
    since_id: str | None,
) -> list[dict]:
    bearer = os.environ["TWITTER_BEARER_TOKEN"]
    params = {
        "max_results": 5,
        "tweet.fields": "created_at,text",
        "exclude": "retweets,replies",
    }
    if since_id:
        params["since_id"] = since_id

    resp = await http.get(
        f"{TWITTER_API_BASE}/users/{user_id}/tweets",
        headers={"Authorization": f"Bearer {bearer}"},
        params=params,
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


async def resolve_user_id(http: httpx.AsyncClient, handle: str) -> str | None:
    bearer = os.environ["TWITTER_BEARER_TOKEN"]
    resp = await http.get(
        f"{TWITTER_API_BASE}/users/by/username/{handle}",
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=10.0,
    )
    if resp.status_code != 200:
        logger.warning("Could not resolve @%s: %s", handle, resp.text)
        return None
    return resp.json()["data"]["id"]


# ── Main poll loop ────────────────────────────────────────────────────────────

async def run(runtime_url: str, poll_interval: int | None) -> None:
    bearer = os.environ.get("TWITTER_BEARER_TOKEN")
    if not bearer:
        print("ERROR: TWITTER_BEARER_TOKEN environment variable is not set.")
        return

    print(f"\n{'='*60}")
    print("AI Feed Watcher")
    print(f"Runtime:  {runtime_url}")
    print(f"Mode:     {'continuous, every ' + str(poll_interval) + 's' if poll_interval else 'single pass'}")
    print(f"Watching: {', '.join('@' + h for h in WATCHED_HANDLES)}")
    print(f"{'='*60}\n")

    async with httpx.AsyncClient() as http:
        user_ids = {}
        for handle in WATCHED_HANDLES:
            uid = await resolve_user_id(http, handle)
            if uid:
                user_ids[handle] = uid
                logger.info("Resolved @%s → %s", handle, uid)

        since_ids: dict[str, str | None] = {h: None for h in user_ids}
        print(f"Resolved {len(user_ids)}/{len(WATCHED_HANDLES)} handles.\n")

        async with httpx.AsyncClient() as runtime_client:
            while True:
                for handle, uid in user_ids.items():
                    try:
                        tweets = await fetch_recent_tweets(http, uid, since_ids[handle])
                        for tweet in tweets:
                            info = await submit_post(
                                runtime_client, runtime_url,
                                handle, tweet["id"], tweet["text"],
                                tweet.get("created_at", ""),
                            )
                            logger.info(
                                "Queued [%s] @%s: %s",
                                info["task_id"][:8], handle, tweet["text"][:60]
                            )
                        if tweets:
                            since_ids[handle] = tweets[0]["id"]
                    except Exception as exc:
                        logger.warning("Error fetching @%s: %s", handle, exc)

                if not poll_interval:
                    break
                logger.info("Poll complete. Sleeping %ds...", poll_interval)
                await asyncio.sleep(poll_interval)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI feed watcher for cuga-runtime")
    parser.add_argument("--runtime-url", default="http://localhost:8001")
    parser.add_argument("--poll-interval", type=int, default=None,
                        help="Seconds between polls. Omit for a single pass.")
    args = parser.parse_args()
    asyncio.run(run(args.runtime_url, args.poll_interval))


if __name__ == "__main__":
    main()
