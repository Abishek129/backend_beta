"""LoggerFast devices + their migrated tables, for the admin's device picker.

Only the PUBLIC routers (``/devices``, ``/tables``), so no credentials are
needed. Cached briefly so rendering an admin form does not cost two HTTP calls
per page load. Never raises: an unreachable logger yields ``{}`` and the admin
falls back to a free-text device id.
"""
import os
import threading
import time

from django.conf import settings

from core.logger_client import LoggerApiError, LoggerClient
from core.models import DataSource

_TTL_S = 30.0
_lock = threading.Lock()
_cache = {'at': 0.0, 'devices': None, 'ok': False}


def _fetch():
    client = LoggerClient(getattr(settings, 'LOGGER_API_URL', ''),
                          timeout=min(float(getattr(settings, 'LOGGER_API_TIMEOUT', 5.0)), 5.0))
    tables_by_dev = {}
    for t in client.tables(status='migrated'):
        dev_id = t.get('deviceId') or t.get('device_id')
        if dev_id and t.get('name'):
            tables_by_dev.setdefault(dev_id, []).append(t)
    out = {}
    for d in client.devices():
        dev_id = d.get('id')
        if not dev_id:
            continue
        tables = tables_by_dev.get(dev_id, [])
        # Same rule as the importer: exactly one migrated table binds; several
        # is ambiguous and binds nothing rather than guessing.
        table = tables[0] if len({t['name'] for t in tables}) == 1 else None
        out[dev_id] = {
            'id': dev_id,
            'name': str(d.get('name') or dev_id),
            'protocol': str(d.get('protocol') or ''),
            'gateway_id': str(d.get('gatewayId') or ''),
            'unit_id': d.get('unitId'),
            'port': d.get('port'),
            'status': str(d.get('status') or ''),
            'table_name': table['name'] if table else '',
            'table_id': str(table.get('id') or '') if table else '',
            'db_target_id': str(table.get('dbTargetId')
                                or (table.get('dbTarget') or {}).get('id') or '') if table else '',
            'ambiguous': len(tables) > 1 and table is None,
        }
    return out


def devices(strict=False):
    """``{device id: {...}}`` from LoggerFast; ``{}`` when it is unreachable —
    or, with ``strict`` (a caller that must not guess), ``LoggerApiError``."""
    with _lock:
        fresh = (_cache['devices'] is not None
                 and time.monotonic() - _cache['at'] < _TTL_S)
        if fresh and (_cache['ok'] or not strict):
            return _cache['devices']
    try:
        rows, ok = _fetch(), True
    except (LoggerApiError, ValueError, TypeError, KeyError) as exc:
        if strict:
            raise LoggerApiError(f'LoggerFast is unreachable: {exc}') from None
        rows, ok = {}, False
    with _lock:
        _cache.update(at=time.monotonic(), devices=rows, ok=ok)
    return rows


def device(dev_id):
    return devices().get(dev_id) if dev_id else None


def data_source_for(db_target_id):
    """The DataSource a LoggerFast dbTargetId is read through.

    Mirrors the importer: its ``logger_<target>`` row when one exists, else the
    row with the same DSN, else a new one on the importer's naming terms.
    """
    if db_target_id:
        row = DataSource.objects.filter(logger_db_target_id=db_target_id).first()
        if row is not None:
            return row
    dsn = (os.environ.get(f'LOGGER_TIMESERIES_DSN_{db_target_id}') if db_target_id else None) \
        or settings.LOGGER_TIMESERIES_DSN
    row = DataSource.objects.filter(dsn=dsn).first()
    if row is not None:
        if db_target_id and not row.logger_db_target_id:
            row.logger_db_target_id = db_target_id
            row.save(update_fields=['logger_db_target_id'])
        return row
    base = f'logger_{db_target_id}' if db_target_id else 'logger_default'
    name, i = base, 2
    while DataSource.objects.filter(name=name).exists():
        name, i = f'{base}-{i}', i + 1
    return DataSource.objects.create(name=name, dsn=dsn, logger_db_target_id=db_target_id,
                                     description='Created by the admin device picker.')
