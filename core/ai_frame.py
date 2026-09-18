"""AiFrameMixin — pushes an `ai` summary frame onto a websocket, fired on connect
and on view-param change (range / sampling / selection), never on plain ticks.

Design: the consumer's send methods call ``_ai_observe(...)`` with the compact
view they just sent. ``_ai_observe`` recomputes a **param signature**; the AI run
fires only when that signature changes — so the first snapshot fires (sig
None→X), repeated ticks keep the same sig (no fire), and a param-change resend
flips the sig (fires). No per-screen ``receive()`` hooks needed.

The AI call goes through :mod:`core.ai_client` (off-thread, never raises). The
follow-up frame is additive (``type:'ai'``) and is sent only when ready, so the
data snapshot is never delayed. The in-flight task is cancelled on disconnect.
"""
import asyncio
import json

from django.conf import settings

from core import ai_client
from core.wire import safe_dumps

# Param attrs that define "the current view"; a change in any re-fires the AI.
# Screens without these (column-row feeders) only ever fire once, on connect.
_SIG_ATTRS = ('range', 'sampling', 'selected_bucket', 'selected_panel')


class AiFrameMixin:
    def _ai_sig(self):
        params = {k: getattr(self, k, None) for k in _SIG_ATTRS}
        return json.dumps([getattr(self, 'PAGE', None), params], sort_keys=True, default=str)

    def _ai_observe(self, *, node_id, title, mfm_type, body):
        """Record the compact view just sent + (maybe) fire the AI summary."""
        if not getattr(settings, 'AI_SUMMARY_ENABLED', True):
            return                                   # disabled -> zero overhead, no task
        page = getattr(self, 'PAGE', '')
        self._ai_node_id = node_id
        self._ai_view = {
            'title': title, 'mfm_type': mfm_type, 'page': page,
            'story': f'{title} — {page} screen ({mfm_type})',
            **(body or {}),
        }
        sig = self._ai_sig()
        if sig == getattr(self, '_ai_prev_sig', None):
            return                                   # unchanged view (a tick) — skip
        self._ai_prev_sig = sig
        prev = getattr(self, '_ai_task', None)
        if prev and not prev.done():
            prev.cancel()
        self._ai_task = asyncio.create_task(self._ai_run(dict(self._ai_view), sig))

    async def _ai_run(self, payload, sig):
        try:
            out = await ai_client.summarize(payload)
        except asyncio.CancelledError:
            return
        except Exception:                            # noqa: BLE001 — never break the socket
            return
        frame = {
            'type': 'ai',
            'mfm_id': getattr(self, '_ai_node_id', None),
            'page': payload.get('page'),
            'param_sig': sig,
            'summary': out.get('summary', ''),
            'source': out.get('source'),
            'model': out.get('model'),
            'latency_ms': out.get('latency_ms'),
        }
        try:
            await self.send(text_data=safe_dumps(frame, default=str))
        except Exception:                            # noqa: BLE001 — socket may have closed
            pass

    def _ai_stop(self):
        t = getattr(self, '_ai_task', None)
        if t and not t.done():
            t.cancel()
