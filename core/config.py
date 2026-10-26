"""Config resolution: per-row override -> type default -> None.

Used by ``/api/mfm/{id}/config/`` (frontend nameplate) and by the consumers for
rated/limit reference lines.

NOMINAL-VOLTAGE CONVENTION (BE-58 — the ONE place the rule is stated):
    * PANEL ids (< FEEDER_ID_OFFSET): `nominal_voltage_v` / `voltage_nominal_v` ship
      LINE-TO-LINE (e.g. 11000, 33000) — the stored config value verbatim.
    * METER ids (>= FEEDER_ID_OFFSET): the same keys ship LINE-TO-NEUTRAL — the
      L-L basis divided by √3 (kept to 1 dp, BE-62, so the FE recovers the exact
      L-L on ×√3).
    A consumer that multiplies a PANEL nominal by √3 will wrongly render 19 kV for an
    11 kV panel — read the value at the level it is served, do NOT re-scale a panel nominal.
"""
import math

from core.config_defaults import defaults_for
from core.constants import FEEDER_ID_OFFSET
from core.models import Group, MFM, NameplateBase
from core.thresholds import OVERVIEW_DEFAULTS, PANEL_DEFAULTS
from core.services import is_real

# A meter with no per-meter override falls back to type defaults sized for a whole
# panel, which flattens its own trace (R1). Derive the ratings from its own
# nameplate (MfmConfig.rated_kva) instead.
_FEEDER_PF = 0.9            # assumed PF for kVA->kW
_LV_LINE_V = 415.0          # LT 3-phase line-to-line for the current rating


def feeder_rating_overrides(rated_capacity_kva, cfg):
    """Return ``cfg`` with the rating fields scaled to the meter's own
    nameplate. No-op when the nameplate is missing so we fall back to the
    (panel-sized) defaults rather than zeros."""
    if not rated_capacity_kva or float(rated_capacity_kva) <= 0:
        return cfg
    kva = float(rated_capacity_kva)
    kw = round(kva * _FEEDER_PF)
    rated_a = round(kva * 1000.0 / (math.sqrt(3) * _LV_LINE_V))
    out = dict(cfg)
    out['rated_kva'] = round(kva, 1)
    out['rated_kw'] = kw
    out['rated_current_a'] = rated_a
    out['current_high_threshold_a'] = round(rated_a * 1.2)   # alarm at 120% of rated
    out['contracted_kw'] = round(kw * 0.9)
    out['critical_load_kw'] = round(kw * 0.5)
    out['energy_target_kwh_today'] = round(kw * 24)   # rated_kw × 24h (100% duty ceiling)
    out['subsidy_limit_mvah'] = None                  # a PCC-level concept, not per-meter
    out['subsidy_limit_kw'] = None
    # Voltage nominal must match the measured basis: the per-phase columns are
    # line-to-NEUTRAL (~240 V), but the default nominal is line-to-line (415 V),
    # which draws the band over the data as a permanent fake violation (0.2).
    # Convert L-L -> L-N so it works at any voltage level (415->240, 11kV->6.35kV).
    nom_ll = cfg.get('voltage_nominal_v') or cfg.get('nominal_voltage_v') or _LV_LINE_V
    # BE-62: keep a DECIMAL L-N nominal — rounding to an integer (415/√3 = 239.6 → 240)
    # then having the FE re-multiply by √3 lands on 415.7 → "416 V". 239.6 recovers to
    # exactly 415. Round to 1 dp for readability without the recompute artifact.
    nom_ln = round(float(nom_ll) / math.sqrt(3), 1)
    out['voltage_nominal_v'] = nom_ln
    out['nominal_voltage_v'] = nom_ln
    return out


# model field -> the key name the CMD_V2 mappers read from /api/mfm/{id}/config/
_FRONTEND_KEYS = {
    'nominal_voltage_v': 'voltage_nominal_v',
    # the PQ mapper reads IEEE-519 names, not our thd_*_limit_pct model names
    'thd_v_limit_pct': 'ieee_519_voltage_thd_limit_pct',
    'thd_i_limit_pct': 'ieee_519_current_thd_limit_pct',
}
# PQ limits the FE mapper expects that we don't store as model fields — emit IEEE
# 519 LV defaults so per-meter limit lines render (FE falls back to these anyway).
_PQ_LIMIT_DEFAULTS = {
    'ieee_519_individual_harmonic_limit_pct': 8.0,
    'flicker_pst_limit': 1.0,
    'crest_factor_ideal': 1.414,
}


def _frontend_config(resolved):
    """Rename/augment keys to the names the frontend mappers expect (keeps the
    model field names too, so both work)."""
    out = dict(resolved)
    for model_key, fe_key in _FRONTEND_KEYS.items():
        if resolved.get(model_key) is not None:
            out[fe_key] = resolved[model_key]
    for k, v in _PQ_LIMIT_DEFAULTS.items():
        out.setdefault(k, v)
    return out


def _overlay(defaults, cfg_row):
    """Type defaults overlaid field-by-field by the row's non-null values."""
    out = {}
    for f in NameplateBase.CONFIG_FIELDS:
        val = getattr(cfg_row, f, None) if cfg_row is not None else None
        out[f] = val if val is not None else defaults.get(f)
    return out


def resolve_panel_config(group):
    """Frontend-ready config for a distribution-panel Group.

    Panel-class defaults overlaid by the group's PanelConfig; the nominal stays
    LINE-TO-LINE verbatim (BE-58).
    """
    # PIN 2026-09-09: every panel_kind (distribution/pcc/ht) is distribution-class,
    # so they share one defaults table; panel_kind only drives the frame mfm_type.
    cfg_row = getattr(group, 'config', None) if group is not None else None
    equipment = getattr(group, 'equipment', None) if group is not None else None
    base = _overlay({**defaults_for('distribution_panel'), **PANEL_DEFAULTS},
                    getattr(equipment, 'config', None))
    return _frontend_config(_overlay(base, cfg_row))


def resolve_mfm_config(mfm):
    """Frontend-ready config for one meter (a single-panel screen set).

    Type defaults for its ``device_type`` overlaid by its MfmConfig; the rating
    fields the row leaves null are derived from ``rated_kva`` with the
    ``feeder_rating_overrides`` arithmetic, and the nominal ships LINE-TO-NEUTRAL
    at 1 dp (BE-62).
    """
    cfg_row = getattr(mfm, 'config', None) if mfm is not None else None
    equipment = getattr(mfm, 'equipment', None) if mfm is not None else None
    equipment_cfg = getattr(equipment, 'config', None) if equipment is not None else None
    equipment_type = None
    if equipment is not None:
        equipment_type = (getattr(getattr(equipment, 'panel_type', None), 'code', None)
                          or getattr(getattr(equipment, 'asset_type', None), 'code', None))
    configured_type = getattr(cfg_row, 'device_type', None)
    # The auto-created MfmConfig default is lt_panel; a more specific equipment
    # type wins over that placeholder. A non-default MfmConfig remains explicit.
    device_type = (configured_type if configured_type and configured_type != 'lt_panel'
                   else equipment_type or configured_type or 'lt_panel')
    out = _overlay(defaults_for(device_type), equipment_cfg)
    out = _overlay(out, cfg_row)

    # Direct MFM fields are the finest-grain nameplate overrides described by
    # additional.md. They deliberately win over equipment and MfmConfig rows.
    direct = {
        'rated_kva': getattr(mfm, 'rated_capacity_kva', None),
        'nominal_voltage_v': getattr(mfm, 'nominal_voltage_v', None),
        'nameplate_voltage_kv': getattr(mfm, 'nameplate_voltage_kv', None),
        'frequency_hz': getattr(mfm, 'frequency_hz', None),
        'fault_level_ka': getattr(mfm, 'fault_level_ka', None),
    }
    for key, value in direct.items():
        if value is not None:
            out[key] = value

    # Derive from the meter's own kVA nameplate for every rating field the row
    # itself left null — same arithmetic as feeder_rating_overrides.
    kva = out.get('rated_kva')
    if kva and float(kva) > 0:
        kva = float(kva)
        kw = round(kva * _FEEDER_PF)
        nominal_ll = float(out.get('nominal_voltage_v') or _LV_LINE_V)
        rated_a = round(kva * 1000.0 / (math.sqrt(3) * nominal_ll))
        derived = {
            'rated_kw': kw,
            'rated_current_a': rated_a,
            'current_high_threshold_a': round(rated_a * 1.2),   # alarm at 120% of rated
        }
        # Only an actual meter capacity supplies these per-meter targets;
        # don't turn generic type defaults into a fictitious nameplate.
        if getattr(mfm, 'rated_capacity_kva', None):
            derived.update(contracted_kw=round(kw * .9), critical_load_kw=round(kw * .5),
                           energy_target_kwh_today=round(kw * 24))
        for key, val in derived.items():
            if getattr(cfg_row, key, None) is None:
                out[key] = val

    out.update(OVERVIEW_DEFAULTS)
    threshold = getattr(mfm, 'thresholds', None)
    if threshold is not None:
        threshold_map = {
            'i_thd_limit_pct': 'thd_i_limit_pct',
            'v_thd_limit_pct': 'thd_v_limit_pct',
            'individual_harmonic_limit_pct': 'ieee_519_individual_harmonic_limit_pct',
            'flicker_pst_limit': 'flicker_pst_limit',
            'crest_factor_ideal': 'crest_factor_ideal',
        }
        for source, target in threshold_map.items():
            value = getattr(threshold, source)
            if value is not None:
                out[target] = value
        for field in threshold._meta.fields:
            if field.name.startswith('overview_'):
                value = getattr(threshold, field.name)
                if value is not None:
                    out[field.name] = value

    breaker = getattr(mfm, 'breaker', None)
    if breaker is not None:
        out['breaker_type'] = breaker.breaker_type
        out['breaker_rating_a'] = breaker.rating_a
    out.update({
        'series_id': getattr(mfm, 'series_id', '') or None,
        'asset_category': getattr(mfm, 'asset_category', '') or None,
        'load_profile': getattr(mfm, 'load_profile', '') or None,
        'parent_series': getattr(mfm, 'parent_series', '') or None,
    })
    logger_schema = getattr(mfm, 'logger_schema', None)
    out['logger_schema'] = ({
        'id': logger_schema.id,
        'code': logger_schema.code,
        'version': logger_schema.version,
        'name': logger_schema.name,
        'timestamp_column': logger_schema.timestamp_column,
        'active': logger_schema.active,
    } if logger_schema is not None else None)

    # L-L -> L-N: the per-phase columns a meter screen plots are line-to-neutral.
    nom_ll = out.get('nominal_voltage_v')
    out['nominal_voltage_ll_v'] = nom_ll
    if nom_ll:
        out['nominal_voltage_v'] = round(float(nom_ll) / math.sqrt(3), 1)
    out['nominal_voltage_ln_v'] = out.get('nominal_voltage_v')
    source = getattr(mfm, 'data_source', None) if mfm is not None else None
    out['voltage_avg_basis'] = 'L-L' if source is not None and is_real(source.dsn) else 'L-N'
    return _frontend_config(out)


def resolve_for_id(eid):
    """Decode a ws/api id to (subject, mfm_type, frontend_config).

    id <  FEEDER_ID_OFFSET -> the active ``is_panel`` Group with that id.
    id >= FEEDER_ID_OFFSET -> the active MFM with id ``eid - FEEDER_ID_OFFSET``.
    Returns (None, None, None) when the id resolves to nothing.
    """
    if eid < FEEDER_ID_OFFSET:
        g = (Group.objects.filter(id=eid, active=True, is_panel=True)
             .select_related('config', 'equipment__config').first())
        if g is None:
            return None, None, None
        kind = getattr(getattr(g, 'config', None), 'panel_kind', None) or 'distribution_panel'
        return g, kind, resolve_panel_config(g)

    m = (MFM.objects.filter(id=eid - FEEDER_ID_OFFSET, active=True)
         .select_related('config', 'data_source', 'equipment__panel_type', 'equipment__asset_type',
                         'equipment__config', 'thresholds', 'breaker',
                         'logger_schema').first())
    if m is None:
        return None, None, None
    device_type = getattr(getattr(m, 'config', None), 'device_type', None)
    mfm_type = 'transformer' if device_type == 'transformer' else 'lt_panel'
    return m, mfm_type, resolve_mfm_config(m)
