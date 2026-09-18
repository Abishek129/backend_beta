"""Wire serialization helpers.

A single non-finite float (NaN/Inf) in a frame makes the consumer ship the
literal token ``NaN``/``Infinity`` — which is INVALID JSON, so the frontend's
``JSON.parse`` throws and the whole tab goes blank (silent, fleet-wide). Postgres
``float8`` columns *can* hold NaN, so we sanitize every frame at the send
boundary: non-finite floats become ``null`` (which the FE already treats as a
gap). Surfaced by tests/fuzz_sockets.py.
"""
import asyncio
import json
import logging
import math

from asgiref.sync import sync_to_async

_ws_log = logging.getLogger('cmd.ws')


def fetch_async(func):
    """``sync_to_async`` WITHOUT ``thread_sensitive`` — for pure-psycopg2
    fetch methods that never touch the Django ORM.

    ``database_sync_to_async`` funnels EVERY call in the process through ONE
    shared thread-sensitive executor. A busy energy-power ``_gather`` (dozens
    of sequential tunnel queries per tick, several sockets) keeps that thread
    at 100%% duty, so every NEW websocket handshake's tiny ORM call queues
    behind seconds-to-minutes of fetch work and times out — observed as a
    fleet-wide "did not receive a valid HTTP response" hang (2026-07-07).
    Pure fetchers run on the default unbounded executor instead; ORM loads
    (``_load_equipment`` etc.) stay on ``database_sync_to_async``."""
    return sync_to_async(func, thread_sensitive=False)


class SafeConnectMixin:
    """Turn an uncaught exception in ``connect()`` into a typed error frame + a
    clean ``4500`` close, instead of Channels' opaque bare ``1011`` (audit H2).

    Wraps the Channels dispatch entrypoint ``websocket_connect`` (which calls
    ``self.connect()``), so it covers every consumer's connect body without
    editing each one. Happy path is unchanged — the wrapper only acts when
    connect raises (e.g. a missing LoggerFast table, a bad param). The client
    gets ``{type:'error', code:4500, message:...}`` and a deliberate close it
    can react to, rather than a silent 1011.
    """
    async def websocket_connect(self, message):
        try:
            await super().websocket_connect(message)
        except Exception as exc:                       # noqa: BLE001 — last-resort guard
            _ws_log.exception('connect failed: %s', self.scope.get('path'))
            frame = safe_dumps({'type': 'error', 'code': 4500,
                                'message': f'{type(exc).__name__}: {str(exc)[:200]}'})
            try:                                       # already accepted -> just send
                await self.send(text_data=frame)
            except Exception:                          # not accepted yet -> accept then send
                try:
                    await self.accept()
                    await self.send(text_data=frame)
                except Exception:
                    pass
            try:
                await self.close(code=4500)
            except Exception:
                pass

    async def _reject(self, code=4404, message='id not served / no meter wired', **extra):
        """Reject a connect *gracefully* — accept, send a typed error frame, then
        close with ``code`` — so the client gets a real close code it can react to.

        Channels turns a bare pre-``accept()`` ``close()`` into an opaque handshake
        **HTTP 403** (the close code is lost), which the FE can't distinguish from an
        auth failure and so retries forever (BE-27: an unwired/offline MFM like the
        idle Tx-Standby incomer). Accepting first makes the 4404 survive to the client.
        ``accept()`` is guarded so this is also safe to call after a prior accept.
        """
        try:
            await self.accept()
        except Exception:                              # already accepted -> fine
            pass
        try:
            await self.send(text_data=safe_dumps({'type': 'error', 'code': code,
                                                  'message': message, **extra}))
        except Exception:
            pass
        try:
            await self.close(code=code)
        except Exception:
            pass

    async def _guarded_loop(self):
        """Run the consumer's push ``_loop`` under a safety net (audit H3/L2).

        The bare loops only catch ``CancelledError``; any other exception (a DB
        blip, a bad param that reached a fetch) escapes, the task dies, and the
        socket is left OPEN but frozen — the FE never learns to reconnect. Here
        a non-cancel failure is logged, a typed error frame is sent, and the
        socket is closed cleanly (4500) so the client can recover. Cancellation
        (normal disconnect) still propagates untouched.
        """
        try:
            await self._loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:                       # noqa: BLE001 — last-resort guard
            _ws_log.exception('tick loop failed: %s', self.scope.get('path'))
            try:
                await self.send(text_data=safe_dumps({'type': 'error', 'code': 4500,
                                'message': f'stream stopped: {type(exc).__name__}'}))
            except Exception:
                pass
            try:
                await self.close(code=4500)
            except Exception:
                pass


def _finite(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def as_int(v):
    """int() that tolerates None/NaN/Inf (event counts under bad input) -> 0."""
    return int(v) if isinstance(v, (int, float)) and math.isfinite(v) else 0


def safe_dumps(obj, **kw):
    """json.dumps with non-finite floats coerced to null (valid JSON always).
    Forwards kwargs (e.g. ``default=str``) so it's a drop-in for json.dumps."""
    kw.setdefault('default', str)
    return json.dumps(_finite(obj), **kw)
