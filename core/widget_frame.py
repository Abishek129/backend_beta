"""Shared "KPI Summary Frame" format — the single source of truth for the
structural shape that similar summary widgets emit.

Only the SHAPE is fixed (headline / progress / metrics / insight + an optional
range envelope, mirroring the FE ``ProgressKpiCard`` primitive). Every key,
label, unit and value is DATA supplied by the widget — an energy card ships kWh,
a demand card ships kW, a cost card ships ₹, all through the same frame.

Adopt in a consumer:
    from core import widget_frame as wf
    frame = wf.kpi_frame(
        headline=wf.headline("Today's Energy", 46258.0, "kWh", target=108000.0),
        progress=wf.progress([wf.segment("active", "Active", 46258.0, "kWh"),
                              wf.segment("reactive", "Reactive", 21072.0, "kVArh")],
                             capacity=108000.0),
        metrics=[wf.metric("sec", "SEC", 29.1, "kWh/t", trend="stable")],
        range_env=wf.range_env("today", start_iso, end_iso, ["today","this_week","this_month"]),
    )
"""


def _round(v, n=2):
    return round(v, n) if isinstance(v, (int, float)) else v


def headline(label, value, unit, target=None, pct_of_target=None, meta=None, tone=None):
    """Primary value of the card. ``pct_of_target`` auto-derives from
    value/target when a positive target is given and pct is not passed."""
    if pct_of_target is None and isinstance(value, (int, float)) \
            and isinstance(target, (int, float)) and target > 0:
        pct_of_target = round(value / target * 100, 1)
    return {
        'label': label,
        'value': _round(value, 2),
        'unit': unit,
        'target': _round(target, 2),
        'pct_of_target': pct_of_target,
        'meta': meta,
        'tone': tone,
    }


def segment(key, label, value, unit, color_role=None):
    """One slice of the progress split (e.g. active vs reactive)."""
    return {'key': key, 'label': label, 'value': _round(value, 2),
            'unit': unit, 'color_role': color_role}


def progress(segments, capacity=None, marker_pct=None):
    """The progress bar: segments summed against ``capacity`` (the denominator);
    ``marker_pct`` optionally places a threshold tick. Returns None when there
    are no segments (a headline-only card)."""
    segs = [s for s in (segments or []) if s is not None]
    if not segs and capacity is None and marker_pct is None:
        return None
    return {'segments': segs, 'capacity': _round(capacity, 2), 'marker_pct': marker_pct}


def metric(key, label, value, unit, tone=None, trend=None):
    """One KPI cell in the metrics strip."""
    return {'key': key, 'label': label, 'value': _round(value, 3),
            'unit': unit, 'tone': tone, 'trend': trend}


def range_env(rng, start, end, available):
    """Optional time-filter envelope. ``start``/``end`` may be datetimes or ISO
    strings; ``available`` is the list of ranges the widget accepts."""
    def iso(x):
        return x.isoformat() if hasattr(x, 'isoformat') else x
    return {'range': rng, 'start': iso(start), 'end': iso(end),
            'available_ranges': list(available or [])}


def kpi_frame(headline, progress=None, metrics=(), insight=None, range_env=None):
    """Assemble the fixed-shape frame body. The consumer wraps this with the
    transport envelope (type/mfm_id/page/ts) when sending."""
    body = {}
    if range_env:
        body.update(range_env)
    body['headline'] = headline
    body['progress'] = progress
    body['metrics'] = [m for m in (metrics or []) if m is not None]
    body['insight'] = insight
    return body
