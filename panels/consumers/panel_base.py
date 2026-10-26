"""Shared plumbing for distribution-panel aggregate WebSockets."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from core.config import resolve_mfm_config, resolve_panel_config
from core.constants import FEEDER_ID_OFFSET
from core.models import Group, Membership
from core.rtm import resolve_thresholds
from core.services import fetch_live
from core.time_filters import (ResolvedTimeFilter, TimeFilterError,
                               TimeFilterPolicy, resolve_time_filter)
from panels.consumers.base import json_value, parse_timestamp

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PanelMember:
    id: int
    eid: int
    name: str
    slug: str
    role: str
    position: int
    dsn: str
    table: str
    time_col: str
    config: dict


@dataclass(frozen=True)
class PanelContext:
    id: int
    name: str
    slug: str
    equipment_key: str
    panel_kind: str
    config: dict
    members: tuple[PanelMember, ...]
    rtm_bands: dict = field(default_factory=dict)


@database_sync_to_async
def panel_context(eid):
    if eid >= FEEDER_ID_OFFSET:
        return None
    group = (Group.objects.filter(pk=eid, active=True, is_panel=True)
             .select_related('config', 'equipment__config', 'equipment__panel_type').first())
    if group is None:
        return None
    rows = (Membership.objects.filter(group=group, mfm__active=True)
            .select_related('mfm__data_source', 'mfm__logger_schema', 'mfm__config',
                            'mfm__equipment__panel_type', 'mfm__equipment__asset_type',
                            'mfm__equipment__config', 'mfm__thresholds', 'mfm__breaker')
            .order_by('position', 'id'))
    members = []
    for membership in rows:
        mfm = membership.mfm
        if not membership.role or mfm.data_source_id is None or not mfm.table_name:
            continue
        members.append(PanelMember(
            id=mfm.id, eid=mfm.eid, name=mfm.name, slug=mfm.slug,
            role=membership.role, position=membership.position,
            dsn=mfm.data_source.dsn, table=mfm.table_name,
            time_col=(mfm.logger_schema.timestamp_column
                      if mfm.logger_schema_id else 'timestamp'),
            config=resolve_mfm_config(mfm),
        ))
    panel_cfg = getattr(group, 'config', None)
    equipment = getattr(group, 'equipment', None)
    return PanelContext(
        id=group.id, name=group.name, slug=group.slug,
        equipment_key=getattr(equipment, 'key', None) or group.slug,
        panel_kind=getattr(panel_cfg, 'panel_kind', None) or 'distribution_panel',
        config=resolve_panel_config(group), members=tuple(members),
        rtm_bands=resolve_thresholds(group),
    )


class PanelConsumer(AsyncJsonWebsocketConsumer):
    page = ''
    poll_seconds = 0

    async def connect(self):
        self.eid = int(self.scope['url_route']['kwargs']['eid'])
        self.ctx = await panel_context(self.eid)
        await self.accept()
        if self.ctx is None:
            await self.send_json({
                'type': 'error', 'code': 4404, 'mfm_id': self.eid,
                'message': 'Distribution panel is missing or inactive.',
            })
            await self.close(code=4404)
            return
        self.raw_filter = {key: values[-1] for key, values in
                           parse_qs(self.scope.get('query_string', b'').decode()).items()}
        self.selected_feeder = None
        self.selected_bucket = None
        self.matrix_focus = None
        try:
            await self.send_json(json_value(await self._build()))
        except Exception as exc:
            await self._send_failure(exc)
            await self.close(code=4500)
            return
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
        log.exception('panel telemetry failed: eid=%s page=%s', self.eid, self.page,
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
                frame['type'] = 'tick'
                await self.send_json(json_value(frame))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._send_failure(exc)

    def identity(self, frame_type='snapshot'):
        return {
            'type': frame_type, 'mfm_id': self.ctx.id, 'mfm_name': self.ctx.name,
            'panel_id': self.ctx.equipment_key, 'mfm_type': self.ctx.panel_kind,
            'page': self.page,
        }

    def latest_anchor(self):
        anchors = []
        for member in self.ctx.members:
            row = fetch_live(member.dsn, member.table, [], member.time_col) or {}
            parsed = parse_timestamp(row.get('ts'))
            if parsed:
                anchors.append(parsed)
        return max(anchors, default=dt.datetime.now(dt.timezone.utc))

    def build_snapshot(self):
        raise NotImplementedError


class FilteredPanelConsumer(PanelConsumer):
    policy = TimeFilterPolicy()

    def resolve_filter(self, *, strict=False) -> ResolvedTimeFilter:
        return resolve_time_filter(self.raw_filter, self.policy, self.latest_anchor(),
                                   strict=strict)

    def apply_command(self, content):
        selected = content.get('selected_period')
        if isinstance(selected, dict) and 'bucket' in selected:
            self.selected_bucket = selected.get('bucket') or None
        selected = content.get('selected_panel')
        if isinstance(selected, dict):
            self.selected_feeder = selected.get('mfm_id')
        elif 'select_feeder' in content:
            selected = content.get('select_feeder')
            self.selected_feeder = (selected.get('mfm_id') if isinstance(selected, dict)
                                    else selected)
        focus = content.get('fleet_matrix')
        if isinstance(focus, dict):
            self.matrix_focus = focus.get('focus') or focus.get('mfm_id')

    async def receive_json(self, content, **kwargs):
        candidate = dict(self.raw_filter)
        timeline = content.get('timeline_filter')
        if isinstance(timeline, dict):
            candidate.update(timeline)
        candidate.update({key: value for key, value in content.items()
                          if key in {'range', 'sampling', 'shift', 'start', 'end',
                                     'start_date', 'end_date'}})
        old_filter = self.raw_filter
        old_state = (self.selected_feeder, self.selected_bucket, self.matrix_focus)
        self.raw_filter = candidate
        try:
            await sync_to_async(self.resolve_filter, thread_sensitive=False)(strict=True)
            self.apply_command(content)
            frame = await self._build()
        except TimeFilterError as exc:
            self.raw_filter = old_filter
            self.selected_feeder, self.selected_bucket, self.matrix_focus = old_state
            await self.send_json({'type': 'error', 'code': 'invalid_filter',
                                  'page': self.page, 'message': str(exc)})
            return
        except Exception as exc:
            self.raw_filter = old_filter
            self.selected_feeder, self.selected_bucket, self.matrix_focus = old_state
            await self._send_failure(exc)
            return
        await self.send_json(json_value(frame))


class LivePanelConsumer(PanelConsumer):
    poll_seconds = getattr(settings, 'MFM_LIVE_POLL_SECONDS', 5)

    async def receive_json(self, content, **kwargs):
        selected = content.get('selected_panel', content.get('select_feeder'))
        if isinstance(selected, dict):
            selected = selected.get('mfm_id')
        if selected in ('', 'clear', None):
            self.selected_feeder = None
        else:
            self.selected_feeder = selected
        try:
            await self.send_json(json_value(await self._build()))
        except Exception as exc:
            await self._send_failure(exc)
