"""
cuga-runtime — Phase 1: async event queue on top of CUGA core.

External callers POST events to /events and get an immediate task_id back.
A background worker picks up each event and calls CUGA's /stream endpoint.
Results are stored in memory and retrievable via GET /status/{task_id}.

No new infrastructure required — just Python + CUGA running somewhere.
"""
