"""
Thin async HTTP client for CUGA's /stream endpoint.

CUGA's /stream returns Server-Sent Events (SSE).
Each event looks like:

    event: Answer
    data: <final answer text>

    event: tool_call
    data: {...}

    event: Error
    data: <error message>

This client reads the stream and returns the first Answer (or raises on Error).
All other event types (intermediate reasoning steps) are ignored in Phase 1.
"""

import json
import httpx
from typing import Optional


# SSE event names we care about
_ANSWER_EVENTS = {"Answer", "FinalAnswer"}
_ERROR_EVENTS = {"Error", "Stopped"}


def _extract_text(payload: str) -> str:
    """
    CUGA's Answer payload is sometimes a JSON object like:
        {"data": "relevant: yes\nsignal: high\n...", "variables": {}, ...}
    Extract the 'data' field when present; otherwise return the raw payload.
    """
    try:
        obj = json.loads(payload)
        if isinstance(obj, dict) and "data" in obj:
            return obj["data"]
    except (json.JSONDecodeError, ValueError):
        pass
    return payload


class CugaClientError(Exception):
    """Raised when CUGA returns an error event or a non-2xx HTTP status."""
    pass


class CugaClient:
    """
    Calls CUGA's POST /stream endpoint and extracts the final answer.

    Usage:
        client = CugaClient(base_url="http://localhost:8000")
        answer = await client.invoke(query="Summarise this lead", thread_id="abc")
    """

    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        """
        Args:
            base_url: Root URL of the CUGA server, e.g. "http://localhost:8000"
            timeout:  How long to wait for CUGA to finish (seconds).
                      Default 300s — agent tasks can take a while.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def invoke(self, query: str, thread_id: Optional[str] = None) -> str:
        """
        Send a query to CUGA and return the final answer text.

        Args:
            query:     The natural-language instruction.
            thread_id: Optional CUGA thread ID (for multi-turn conversations).

        Returns:
            The answer string from CUGA.

        Raises:
            CugaClientError: If CUGA returns an error or the HTTP call fails.
        """
        headers = {"Content-Type": "application/json"}
        if thread_id:
            headers["X-Thread-ID"] = thread_id

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            async with http.stream(
                "POST",
                f"{self.base_url}/stream",
                json={"query": query},
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise CugaClientError(
                        f"CUGA returned HTTP {response.status_code}: {body.decode()[:500]}"
                    )
                return await self._parse_sse_stream(response)

    async def _parse_sse_stream(self, response: httpx.Response) -> str:
        """
        Read SSE lines and return the full data payload of the first Answer event.

        SSE format (per spec):
          event: EventName
          data: first line of data
          data: second line of data    ← multiple data: lines are joined with \n
                                        ← blank line signals end of event block
        CUGA also sends multi-line answers as a single data: field with embedded
        newlines — both formats are handled here.
        """
        current_event_name: Optional[str] = None
        data_lines: list[str] = []

        async for line in response.aiter_lines():
            # Blank line = end of this SSE event block
            if line.strip() == "":
                if current_event_name and data_lines:
                    payload = "\n".join(data_lines)
                    if current_event_name in _ANSWER_EVENTS:
                        return _extract_text(payload)
                    if current_event_name in _ERROR_EVENTS:
                        raise CugaClientError(f"CUGA error event: {payload}")
                # Reset for next event
                current_event_name = None
                data_lines = []
                continue

            if line.startswith("event: "):
                current_event_name = line[len("event: "):]
                data_lines = []

            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])

            else:
                # Continuation line (no prefix) — append to current data
                data_lines.append(line)

        # Handle stream that ends without a trailing blank line
        if current_event_name and data_lines:
            payload = "\n".join(data_lines)
            if current_event_name in _ANSWER_EVENTS:
                return _extract_text(payload)
            if current_event_name in _ERROR_EVENTS:
                raise CugaClientError(f"CUGA error event: {payload}")

        raise CugaClientError("CUGA stream ended without an Answer event")
