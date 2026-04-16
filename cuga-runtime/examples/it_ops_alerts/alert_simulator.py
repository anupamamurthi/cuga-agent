"""
IT Ops alert simulator.

Simulates what a monitoring system (Datadog, PagerDuty, Prometheus) would do
when an alert fires: POST to cuga-runtime's /events endpoint and track results.

Run against a live runtime:
    python alert_simulator.py --runtime-url http://localhost:8001 --live

Or in dry-run mode (just prints the events it would send):
    python alert_simulator.py --dry-run
"""

import asyncio
import argparse
import json
import sys
import time

import httpx

from examples.it_ops_alerts.sample_alerts import ALL_ALERTS, OPS_TRIAGE_PROMPT


async def fire_alert(
    client: httpx.AsyncClient,
    runtime_url: str,
    alert: dict,
    persona: str = "ops_agent",
) -> dict:
    """
    Fire one alert event to the runtime.
    Returns the submitted task info { task_id, status }.
    """
    event = {
        "query": OPS_TRIAGE_PROMPT,
        "persona": persona,
        "trigger": alert["trigger"],
        "context": alert["context"],
    }
    resp = await client.post(f"{runtime_url}/events", json=event)
    resp.raise_for_status()
    return resp.json()


async def poll_result(
    client: httpx.AsyncClient,
    runtime_url: str,
    task_id: str,
    timeout: float = 120.0,
) -> dict:
    """Poll /status until completed or failed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get(f"{runtime_url}/events/{task_id}")
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(1.0)
        print(f"  [{task_id[:8]}] {body['status']}...", flush=True)
    raise TimeoutError(f"Task {task_id} did not finish in {timeout}s")


async def simulate(runtime_url: str, dry_run: bool = False) -> None:
    """Fire all sample alerts and print the triage results."""
    print(f"\n{'='*60}")
    print("IT Ops Alert Simulator")
    print(f"Runtime: {runtime_url}")
    print(f"Mode: {'DRY RUN (no HTTP calls)' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    if dry_run:
        for alert in ALL_ALERTS:
            print(f"Would fire: {alert['trigger']}")
            print(f"  service: {alert['context'].get('service', 'unknown')}")
            print(f"  context keys: {list(alert['context'].keys())}\n")
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fire all alerts without waiting — this is the point of async
        print("Firing all alerts simultaneously (async)...\n")
        tasks_fired = []
        for alert in ALL_ALERTS:
            task_info = await fire_alert(client, runtime_url, alert)
            print(f"  Queued [{task_info['task_id'][:8]}] trigger={alert['trigger']}")
            tasks_fired.append((alert, task_info))

        print(f"\nAll {len(tasks_fired)} alerts queued. Waiting for CUGA to triage...\n")

        # Poll all in parallel
        results = await asyncio.gather(*[
            poll_result(client, runtime_url, info["task_id"])
            for _, info in tasks_fired
        ], return_exceptions=True)

        # Print triage summaries
        print(f"\n{'='*60}")
        print("TRIAGE RESULTS")
        print(f"{'='*60}\n")
        for (alert, info), result in zip(tasks_fired, results):
            print(f"Alert:   {alert['trigger']}")
            print(f"Service: {alert['context'].get('service', 'unknown')}")
            print(f"Status:  {result.get('status', 'ERROR') if isinstance(result, dict) else str(result)}")
            if isinstance(result, dict) and result.get("output"):
                print(f"CUGA says:\n{result['output']}")
            elif isinstance(result, Exception):
                print(f"ERROR: {result}")
            print(f"{'-'*40}\n")


def main():
    parser = argparse.ArgumentParser(description="IT Ops alert simulator for cuga-runtime")
    parser.add_argument("--runtime-url", default="http://localhost:8001")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(simulate(args.runtime_url, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
