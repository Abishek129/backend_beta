from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from core.config import resolve_mfm_config
from core.constants import FEEDER_ID_OFFSET
from core.models import MFM
from core.services import fetch_live
from core.time_filters import (ResolvedTimeFilter, TimeFilterError,
                               TimeFilterPolicy, resolve_time_filter)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MeterContext:
    id: int
    eid: int
    name: str
    slug: str
    panel_id: str
    mfm_type: str
    dsn: str
    table: str
    time_col: str
    config: dict


@database_sync_to_async
def meter_context(eid):
    if eid < FEEDER_ID_OFFSET:
        return None
    mfm = (MFM.objects.filter(pk=eid - FEEDER_ID_OFFSET, active=True)
           .select_related('data_source', 'logger_schema', 'config', 'equipment__panel_type',
                           'equipment__asset_type', 'equipment__config', 'thresholds',
                           'breaker').first())
    if mfm is None or mfm.data_source_id is None or not mfm.table_name:
        return None
    cfg_row = getattr(mfm, 'config', None)
    equipment = getattr(mfm, 'equipment', None)
    equipment_type = (getattr(getattr(equipment, 'panel_type', None), 'code', None)
                      or getattr(getattr(equipment, 'asset_type', None), 'code', None))
    configured = getattr(cfg_row, 'device_type', None)
    mfm_type = (configured if configured and configured != 'lt_panel'
                else equipment_type or configured or 'lt_panel')
    if mfm_type not in {'transformer', 'lt_panel'}:
        mfm_type = 'lt_panel'
    return MeterContext(
        id=mfm.id, eid=eid, name=mfm.name, slug=mfm.slug,
        panel_id=getattr(equipment, 'key', None) or mfm.slug,
        mfm_type=mfm_type, dsn=mfm.data_source.dsn, table=mfm.table_name,
        time_col=(mfm.logger_schema.timestamp_column if mfm.logger_schema_id else 'timestamp'),
        config=resolve_mfm_config(mfm),
    )


def json_value(value):
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def average(*values):
    values = [number(value, None) for value in values if value is not None]
    return sum(values) / len(values) if values else None


def parse_timestamp(value):
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed


class SingleMFMConsumer(AsyncJsonWebsocketConsumer):
    page = ''
    poll_seconds = 15

    async def connect(self):
        self.eid = int(self.scope['url_route']['kwargs']['eid'])
        self.ctx = await meter_context(self.eid)
        await self.accept()
        if self.ctx is None:
            await self.send_json({
                'type': 'error', 'code': 4404, 'mfm_id': self.eid,
                'message': 'MFM is missing, inactive, or has no timeseries binding.',
            })
            await self.close(code=4404)
            return
        self.raw_filter = {key: values[-1] for key, values in
                           parse_qs(self.scope.get('query_string', b'').decode()).items()}
        try:
            frame = await self._build()
        except Exception as exc:
            await self._send_failure(exc)
            await self.close(code=4500)
            return
        await self.send_json(json_value(frame))
        self.poll_task = None
        if self.poll_seconds:
            self.poll_task = asyncio.create_task(self._poll())

    async def disconnect(self, close_code):
        task = getattr(self, 'poll_task', None)
        if task:
            task.cancel()

    async def _build(self):
        return await sync_to_async(self.build_snapshot, thread_sensitive=False)()

    async def _send_failure(self, exc):
        log.exception('single-MFM telemetry failed: eid=%s page=%s', self.eid, self.page,
                      exc_info=exc)
        await self.send_json({
            'type': 'error', 'code': 'telemetry_error', 'mfm_id': self.eid,
            'page': self.page, 'message': 'Telemetry is temporarily unavailable.',
        })

    async def _poll(self):
        try:
            while True:
                await asyncio.sleep(self.poll_seconds)
                frame = await self._build()
                frame['type'] = 'update'
                await self.send_json(json_value(frame))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._send_failure(exc)

    def identity(self, frame_type='snapshot'):
        return {
            'type': frame_type, 'mfm_id': self.ctx.eid, 'mfm_name': self.ctx.name,
            'panel_id': self.ctx.panel_id, 'mfm_type': self.ctx.mfm_type,
            'page': self.page,
        }

    def build_snapshot(self):
        raise NotImplementedError


class FilteredMFMConsumer(SingleMFMConsumer):
    policy = TimeFilterPolicy()
    poll_seconds = 0

    def resolve_filter(self, *, strict=False) -> ResolvedTimeFilter:
        latest = fetch_live(self.ctx.dsn, self.ctx.table, [], self.ctx.time_col) or {}
        anchor = parse_timestamp(latest.get('ts')) or dt.datetime.now(dt.timezone.utc)
        return resolve_time_filter(self.raw_filter, self.policy, anchor, strict=strict)

    async def receive_json(self, content, **kwargs):
        candidate = dict(self.raw_filter)
        candidate.update({key: value for key, value in content.items()
                          if key in {'range', 'sampling', 'shift', 'start', 'end',
                                     'start_date', 'end_date'}})
        old = self.raw_filter
        self.raw_filter = candidate
        try:
            # Resolve first: an invalid command must not replace the current filter.
            await sync_to_async(self.resolve_filter, thread_sensitive=False)(strict=True)
            frame = await self._build()
        except TimeFilterError as exc:
            self.raw_filter = old
            await self.send_json({
                'type': 'error', 'code': 'invalid_filter', 'page': self.page,
                'message': str(exc),
            })
            return
        except Exception as exc:
            self.raw_filter = old
            await self._send_failure(exc)
            return
        await self.send_json(json_value(frame))


class QueueMFMConsumer(SingleMFMConsumer):
    columns = ()
    window_seconds = 900
    capacity = 12
    poll_seconds = getattr(settings, 'MFM_LIVE_POLL_SECONDS', 5)

    def shape_row(self, row):
        return {key: (value if key == 'ts' else number(value, None))
                for key, value in row.items()}

    @staticmethod
    def _status_map(row):
        return {key: value for key, value in row.items()
                if key.endswith('_status') and isinstance(value, str) and value}

    def build_snapshot(self):
        from core.services import fetch_window
        rows = fetch_window(
            self.ctx.dsn, self.ctx.table, self.columns,
            self.window_seconds, self.ctx.time_col)
        queue = [self.shape_row(row) for row in rows[-self.capacity:]]
        self.last_ts = queue[-1].get('ts') if queue else None
        return {
            **self.identity(), 'window_seconds': self.window_seconds,
            'capacity': self.capacity, 'columns': list(self.columns),
            'count': len(queue), 'queue': queue,
            'status': self._status_map(queue[-1]) if queue else {},
        }

    async def _poll(self):
        try:
            while True:
                await asyncio.sleep(self.poll_seconds)
                row = await sync_to_async(fetch_live, thread_sensitive=False)(
                    self.ctx.dsn, self.ctx.table, self.columns, self.ctx.time_col)
                if not row or row.get('ts') == getattr(self, 'last_ts', None):
                    continue
                self.last_ts = row.get('ts')
                shaped = await sync_to_async(self.shape_row, thread_sensitive=False)(row)
                await self.send_json(json_value({
                    'type': 'tick', 'enqueue': [shaped], 'dequeue': 1,
                    'queue_size': self.capacity, 'status': self._status_map(shaped),
                }))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._send_failure(exc)
