"""Resolve RTM band thresholds for a panel: per-group override -> code default
(core.rtm_defaults.DEFAULT_BANDS)."""
from core.rtm_defaults import DEFAULT_BANDS, METRIC_META


def resolve_thresholds(group):
    """{metric: (low_max, normal_max, moderate_max, high_max)} for a Group."""
    rows = {t.metric: t for t in group.rtm_thresholds.all()} if group else {}
    out = {}
    for metric in METRIC_META:
        row = rows.get(metric)
        out[metric] = row.bounds() if row else DEFAULT_BANDS[metric]
    return out
