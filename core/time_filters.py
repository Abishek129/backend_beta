"""Shared, immutable time-filter resolution for single-MFM endpoints."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.services import (custom_samplings, custom_window, normalize_range, range_full,
                           resolve_range)


ALL_RANGES = (
    'today', 'yesterday', 'this_week', 'last_7d', 'this_month',
    'last_month', 'last_30d', 'last_24h', 'custom',
)
SAMPLING_BY_RANGE = {
    'today': ('hour', 'hourly', '2hour', 'shift'),
    'yesterday': ('hour', 'hourly', '2hour', 'shift'),
    'this_week': ('day', 'shift'),
    'last_7d': ('day', 'shift'),
    'this_month': ('day', 'week'),
    'last_month': ('day', 'week'),
    'last_30d': ('day', 'week'),
    'last_24h': ('hour', 'hourly', '2hour', 'shift'),
}
DEFAULT_SAMPLING = {
    'today': 'hour', 'yesterday': 'hour', 'this_week': 'day',
    'last_7d': 'day', 'this_month': 'day', 'last_month': 'day',
    'last_30d': 'day', 'last_24h': 'hour', 'custom': 'day',
}
SAMPLING_ALIASES = {'daily': 'day', 'weekly': 'week', 'monthly': 'month',
                    'hourly_1h': 'hour'}


class TimeFilterError(ValueError):
    pass


@dataclass(frozen=True)
class TimeFilterPolicy:
    default_range: str = 'today'
    allowed_ranges: tuple[str, ...] = ALL_RANGES
    supports_sampling: bool = True
    supports_shift: bool = True


@dataclass(frozen=True)
class ResolvedTimeFilter:
    range: str
    start: datetime
    end: datetime
    full_start: datetime
    full_end: datetime
    sampling: str | None
    shift: str | None
    available_ranges: tuple[str, ...]
    available_samplings: tuple[str, ...]
    sampling_by_range: dict[str, tuple[str, ...]]
    available_shifts: tuple[str, ...]

    def envelope(self):
        return {
            'range': self.range,
            'start': self.start.isoformat(),
            'end': self.end.isoformat(),
            'sampling': self.sampling,
            'shift': self.shift,
            'available_ranges': list(self.available_ranges),
            'available_samplings': list(self.available_samplings),
            'sampling_by_range': {key: list(value)
                                  for key, value in self.sampling_by_range.items()},
            'available_shifts': list(self.available_shifts),
        }


def resolve_time_filter(raw, policy: TimeFilterPolicy, anchor_now: datetime,
                        *, strict=False) -> ResolvedTimeFilter:
    requested = raw.get('range') or policy.default_range
    canonical = normalize_range(requested)
    raw_token = str(requested).lower().replace('-', '_')
    if strict and canonical == 'today' and raw_token != 'today':
        raise TimeFilterError(f'range {requested!r} is not supported')
    if canonical not in policy.allowed_ranges:
        if strict:
            raise TimeFilterError(f'range {requested!r} is not supported')
        canonical = policy.default_range
    start_raw, end_raw = raw.get('start') or raw.get('start_date'), raw.get('end') or raw.get('end_date')
    if canonical == 'custom' and (not start_raw or not end_raw):
        if strict:
            raise TimeFilterError('custom range requires both start and end')
        canonical = policy.default_range
    if strict and canonical == 'custom' and custom_window(start_raw, end_raw) == (None, None):
        raise TimeFilterError('custom range bounds are invalid')
    start, end = resolve_range(canonical, anchor_now, start_raw, end_raw)
    if end <= start:
        raise TimeFilterError('time range must have a positive duration')
    full_start, full_end = range_full(canonical, anchor_now, start_raw, end_raw)

    if policy.supports_sampling:
        allowed = (custom_samplings(start, end) if canonical == 'custom'
                   else SAMPLING_BY_RANGE.get(canonical, ('day',)))
        requested_sampling = raw.get('sampling') or DEFAULT_SAMPLING.get(canonical, allowed[0])
        requested_sampling = SAMPLING_ALIASES.get(str(requested_sampling).lower(),
                                                  requested_sampling)
        sampling = requested_sampling if requested_sampling in allowed else None
        if sampling is None:
            if strict and raw.get('sampling'):
                raise TimeFilterError(
                    f'sampling {requested_sampling!r} is not valid for {canonical}')
            sampling = DEFAULT_SAMPLING.get(canonical)
            if sampling not in allowed:
                sampling = allowed[0]
    else:
        allowed, sampling = (), None
        if strict and raw.get('sampling'):
            raise TimeFilterError('sampling is not supported by this endpoint')

    shifts = ('all', 'a', 'b', 'c') if policy.supports_shift else ()
    shift = (raw.get('shift') or 'all').lower() if policy.supports_shift else None
    if shift not in shifts:
        if strict:
            raise TimeFilterError(f'shift {shift!r} is not supported')
        shift = 'all'
    if strict and not policy.supports_shift and raw.get('shift'):
        raise TimeFilterError('shift is not supported by this endpoint')
    return ResolvedTimeFilter(
        range=canonical, start=start, end=end, full_start=full_start,
        full_end=full_end, sampling=sampling, shift=shift,
        available_ranges=policy.allowed_ranges,
        available_samplings=tuple(allowed),
        sampling_by_range={key: tuple(value) for key, value in SAMPLING_BY_RANGE.items()
                           if key in policy.allowed_ranges},
        available_shifts=shifts,
    )
