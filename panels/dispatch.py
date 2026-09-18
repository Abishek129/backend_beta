"""Equipment-type dispatch for the shared ``ws/mfm/{id}/{screen}/`` namespace.

Three equipment types, three screen sets:

    distribution_panel -> aggregate fan-out consumers
    transformer        -> transformer feeder consumers (column-row + history)
    lt_panel           -> lt_panel feeder consumers   (column-row + history)

The type is resolved from the id once at connect:
    id <  FEEDER_ID_OFFSET -> an active is_panel Group (a distribution panel)
    id >= FEEDER_ID_OFFSET -> an active MFM; transformer if its MfmConfig says
                              so, else lt_panel.

Each screen wires only the types it serves (e.g. energy-distribution is
distribution-only; overview/histories are feeder-only); an id whose type isn't
served on that screen is rejected with close code 4404.
"""
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from core.constants import FEEDER_ID_OFFSET


@database_sync_to_async
def _resolve_type(eid):
    # PIN 2026-09-09: beta resolves off the imported hierarchy — Group.is_panel
    # for panels, MfmConfig.device_type for meters. Retired rows resolve to None.
    from core.models import MFM, Group
    if eid < FEEDER_ID_OFFSET:
        g = Group.objects.filter(id=eid, active=True, is_panel=True).first()
        return 'distribution_panel' if g is not None else None
    m = (MFM.objects.filter(id=eid - FEEDER_ID_OFFSET, active=True)
         .select_related('config').first())
    if m is None:
        return None
    cfg = getattr(m, 'config', None)
    if cfg is not None and cfg.device_type == 'transformer':
        return 'transformer'
    return 'lt_panel'


class _Reject(AsyncWebsocketConsumer):
    """Id/type not served on this screen (a feeder-only screen opened on a panel
    id, an unknown/invalid id, or an unknown screen). We ACCEPT then send a typed
    ``error`` frame and close with app code 4404 — so the FE gets a clean WS close
    with the code + reason, NOT an opaque handshake 403 (which surfaces as
    `read ECONNRESET` through the Vite proxy) or a 500 on a non-numeric id."""
    async def connect(self):
        ctx = self.scope.get('_reject', {})
        await self.accept()
        await self.send(text_data=json.dumps({
            'type': 'error', 'code': 4404,
            'mfm_id': ctx.get('eid'),
            'mfm_type': ctx.get('mfm_type'),
            'served_types': ctx.get('served', []),
            'message': ctx.get('message', 'id not served on this screen'),
        }))
        await self.close(code=4404)


_reject_app = _Reject.as_asgi()


def _reject_scope(scope, **ctx):
    scope = dict(scope)
    scope['_reject'] = ctx
    return scope


def type_dispatch(*, distribution_panel=None, transformer=None, lt_panel=None):
    apps = {
        'distribution_panel': distribution_panel.as_asgi() if distribution_panel else None,
        'transformer': transformer.as_asgi() if transformer else None,
        'lt_panel': lt_panel.as_asgi() if lt_panel else None,
    }
    served = [k for k, v in apps.items() if v]

    async def app(scope, receive, send):
        raw = scope['url_route']['kwargs'].get('eid')
        try:
            eid = int(raw)
        except (TypeError, ValueError):
            eid = None
        t = await _resolve_type(eid) if eid is not None else None
        chosen = apps.get(t)
        if chosen is None:
            msg = (f"id {raw} (type {t or 'unknown/invalid'}) not served on this screen; "
                   f"served types: {', '.join(served) or 'none'}")
            return await _reject_app(_reject_scope(scope, eid=raw, mfm_type=t, served=served,
                                                  message=msg), receive, send)
        return await chosen(scope, receive, send)

    return app


def reject_invalid_id():
    """Catch-all ASGI app for ``ws/mfm/<str:eid>/<str:screen>/`` — any path whose
    id isn't a valid numeric equipment/feeder id (or an unknown screen). Cleanly
    rejects (accept -> error frame -> close 4404) instead of the URLRouter raising
    ``No route found`` (which daphne surfaces as a 500)."""
    async def app(scope, receive, send):
        raw = scope['url_route']['kwargs'].get('eid')
        msg = (f"'{raw}' is not a valid mfm id — expected a numeric equipment id "
               f"or a feeder id (FEEDER_ID_OFFSET + MFM.id). Category/group nodes "
               f"(e.g. 'asset-transformers') are not sockets.")
        return await _reject_app(_reject_scope(scope, eid=raw, mfm_type=None, served=[],
                                              message=msg), receive, send)
    return app
