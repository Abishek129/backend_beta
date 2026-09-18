"""Async client for the graph_summary service (separate process, :8077).

Posts a compact graph/widget payload and returns the one-paragraph summary. The
HTTP call is stdlib ``urllib`` run in a worker thread (``asyncio.to_thread``) so
it never blocks the Channels event loop, and it NEVER raises — on any
error/timeout/disabled it returns ``{'summary': '', 'source': 'unavailable'}``.
This mirrors the graph_summary ``summarize()`` contract so consumers can ship the
result straight onto the wire.
"""
import asyncio
import json
import urllib.error
import urllib.request

from django.conf import settings


_sems = {}                                       # one Semaphore per event loop


def _sem():
    """Bound concurrent summary calls per running loop (a module-global semaphore
    can't cross the fresh loops the test harnesses spin up)."""
    loop = asyncio.get_running_loop()
    s = _sems.get(loop)
    if s is None:
        n = int(getattr(settings, 'AI_SUMMARY_CONCURRENCY', 3))
        s = _sems[loop] = asyncio.Semaphore(max(1, n))
    return s


def _post(url, payload, timeout):
    data = json.dumps(payload, default=str).encode()
    req = urllib.request.Request(
        url.rstrip('/') + '/summarize', data=data,
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


async def summarize(payload):
    """Return the summary dict for ``payload`` (a JSON-able view of the screen).
    Never raises — failures come back as ``source='unavailable'`` so the caller
    can ship an honest "—" without special-casing exceptions."""
    if not getattr(settings, 'AI_SUMMARY_ENABLED', True):
        return {'summary': '', 'source': 'disabled'}
    url = getattr(settings, 'AI_SUMMARY_URL', 'http://127.0.0.1:8077')
    timeout = float(getattr(settings, 'AI_SUMMARY_TIMEOUT', 8))
    try:
        async with _sem():                       # bound concurrent LLM load
            out = await asyncio.to_thread(_post, url, payload, timeout)
        if isinstance(out, dict) and 'summary' in out:
            return out
        return {'summary': '', 'source': 'unavailable', 'error': 'bad response shape'}
    except Exception as e:                       # noqa: BLE001 — transport/timeout/JSON
        return {'summary': '', 'source': 'unavailable', 'error': str(e)[:200]}
