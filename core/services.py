"""Read-only timeseries broker over the external DB(s).

One psycopg2 connection pool per DataSource DSN, so the socket layer pools a
single set of connections per source instead of one per meter. Never writes.
"""
import datetime as dt
import logging
import os
import re
import threading

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

log = logging.getLogger(__name__)

_IDENT = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
_pools = {}
_colcache = {}
_boolcache = {}
_timetypecache = {}
_TEXT_TYPES = {'character varying', 'text', 'character', 'char', 'bpchar'}
_lock = threading.Lock()


def _pool(dsn):
    with _lock:
        p = _pools.get(dsn)
        if p is None:
            # TCP keepalives + connect timeout: the real logger rides an SSH
            # tunnel over the FortiGate VPN, and a VPN re-dial silently kills
            # every established conn. Without keepalives a query on a dead conn
            # blocks its thread for hours — and one blocked database_sync_to_-
            # async call starves the shared thread-sensitive executor, hanging
            # EVERY websocket connect in the process (observed 2026-07-07).
            p = ThreadedConnectionPool(
                1, 16, dsn=dsn,
                connect_timeout=10,
                keepalives=1, keepalives_idle=30,
                keepalives_interval=10, keepalives_count=3,
            )
            _pools[dsn] = p
        return p


def _safe(names):
    bad = [n for n in names if not _IDENT.match(n)]
    if bad:
        raise ValueError(f'unsafe identifier(s): {bad}')


def _columns(conn, dsn, table):
    """Column names for ``table`` in the connection's effective search_path —
    NOT hardcoded to ``public`` (a logger DB may own its tables in another
    schema, e.g. ``neuract``, selected via the DSN's ``options=-csearch_path``)."""
    key = (dsn, table)
    cols = _colcache.get(key)
    if cols is None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s AND table_schema = ANY(current_schemas(false))", (table,))
            cols = {r[0] for r in cur.fetchall()}
        _colcache[key] = cols
    return cols


def columns(dsn, table):
    """Public: the set of column names for ``table`` (schema-aware, cached).
    Lets a consumer detect which of its expected columns the real logger table
    actually has, to resolve a schema-alias bridge."""
    _safe([table])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        return set(_columns(conn, dsn, table))
    finally:
        p.putconn(conn)


def _bool_cols(conn, dsn, table):
    """Names of the BOOLEAN-typed columns in ``table``. Real loggers store the
    ``*_event_active`` flags as ``real`` (0.0/1.0) not ``boolean``, so callers
    that treat a flag as truthy must coerce non-boolean ones (``col <> 0``)."""
    key = (dsn, table)
    cols = _boolcache.get(key)
    if cols is None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=%s AND data_type='boolean' "
                "AND table_schema = ANY(current_schemas(false))", (table,))
            cols = {r[0] for r in cur.fetchall()}
        _boolcache[key] = cols
    return cols


def _time_is_text(conn, dsn, table, time_col):
    """True when the time column is stored as TEXT (ISO-8601 string) rather than
    a native timestamp/timestamptz — some loggers store ``timestamp_utc`` as
    varchar. Cached per (dsn, table, col)."""
    key = (dsn, table, time_col)
    v = _timetypecache.get(key)
    if v is None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name=%s AND column_name=%s "
                "AND table_schema = ANY(current_schemas(false)) LIMIT 1", (table, time_col))
            r = cur.fetchone()
        v = _timetypecache[key] = 'text' if (r and (r[0] or '') in _TEXT_TYPES) else 'native'
    return v == 'text'


def _texpr(time_col, is_text):
    """SQL reference for the time column. A TEXT ISO column is cast to
    ``timestamptz`` (so date math / AT TIME ZONE / range compares work); a native
    column stays a bare identifier so its btree index is still usable."""
    return f'("{time_col}")::timestamptz' if is_text else f'"{time_col}"'


_timecolcache = {}
# Loggers name the time column differently — try the requested one first, then
# these known alternates (a meter table from another logger uses `timestamp_utc`).
_TIME_ALTS = ('timestamp', 'timestamp_utc', 'ts', 'time', 'datetime')


def _resolve_time_col(conn, dsn, table, time_col):
    """The actual time column on ``table``: the requested name if present, else
    the first known alternate that exists. So consumers keep passing the default
    ``'timestamp'`` and a ``timestamp_utc`` table just works. Cached."""
    key = (dsn, table, time_col)
    v = _timecolcache.get(key)
    if v is None:
        have = _columns(conn, dsn, table)
        v = _timecolcache[key] = (time_col if time_col in have
                                  else next((a for a in _TIME_ALTS if a in have), time_col))
    return v


def _tcol(conn, dsn, table, time_col):
    """(name, cast_expr, where_expr, bound_fn).

    - ``cast_expr`` = ``("col")::timestamptz`` for a TEXT ISO column, else ``"col"``
      — use in SELECT / date_trunc / AT TIME ZONE / epoch (needs a real timestamp).
    - ``where_expr`` = the RAW ``"col"`` — use in WHERE / ORDER BY so the column's
      btree index is usable (the cast defeats it -> full seq scan).
    - ``bound_fn(dt)`` = how to bind a comparison bound: for a TEXT (IST ISO) column
      it returns the IST ISO string (text order == chronological, index-usable);
      for a native column it returns the datetime unchanged.

    The gic loggers store ``timestamp_utc`` as TEXT ISO-8601 with a fixed +05:30
    offset, so lexical compare == chronological — verified identical to the cast."""
    rc = _resolve_time_col(conn, dsn, table, time_col)
    is_text = _time_is_text(conn, dsn, table, rc)
    tc = _texpr(rc, is_text)
    wc = f'"{rc}"'
    bnd = (lambda d: d.astimezone(_IST_TZ).isoformat()) if is_text else (lambda d: d)
    return rc, tc, wc, bnd


# ── Schema aliases — canonical column → the real logger's differently-named
# column(s). Consumers read the canonical (simulator-era) names; a real meter
# family (e.g. PM8000) may log the same quantity under per-phase (`_a/_b/_c`) or
# `_delivered_` names, or not at all. Applied ONLY when the canonical column is
# ABSENT, so it is purely additive — it never changes a value that already exists
# (the sim, which has the canonical name, never hits the alias).
#   candidate = str                    → a direct rename (used if that column exists)
#   candidate = ('mean'|'max', [cols]) → per-phase aggregate (all cols must exist)
_SCHEMA_ALIAS = {
    'apparent_energy_kvah': ['apparent_energy_delivered_kvah', 'apparent_energy_import_kvah'],
    'k_factor':             [('mean', ['k_factor_a', 'k_factor_b', 'k_factor_c'])],
    'crest_factor_voltage': [('mean', ['crest_factor_a', 'crest_factor_b', 'crest_factor_c'])],

    # ── BMS Air Compressor: canonical (simulator-era) name -> the real LoggerFast
    #    column. Safe to keep global: every candidate below exists ONLY on the
    #    `air_compressor_*` tables (verified against both DBs), so it can never
    #    fire on an EMS panel, DG/UPS register or transformer table — and it is
    #    only consulted when the canonical column is ABSENT, so the simulator
    #    (which has the canonical names) never hits it. Source: bms/air_compressors.md
    #    Category 1. `bearing_temp_c` + `aftercooler_temp_c` need no entry — the
    #    logger already derives them under the canonical names.
    'drive_motor_temp_c':            ['drive_motor_temp'],
    'u_winding_temp_c':              ['motor_winding_1u1'],
    'v_winding_temp_c':              ['motor_winding_1v1'],
    'w_winding_temp_c':              ['motor_winding_1w1'],
    # `motor_winding_temp_average` is the UINT32 sentinel in 100% of rows on this
    # logger, so prefer the mean of the three phase windings (all populated);
    # the average register stays as the fallback for a unit that does log it.
    'winding_temp_c':                [('mean', ['motor_winding_1u1', 'motor_winding_1v1',
                                                'motor_winding_1w1']),
                                     'motor_winding_temp_average'],
    'direct_end_bearing_temp_c':     ['motor_bearing_d_end'],
    'non_direct_end_bearing_temp_c': ['motor_bearing_nd_end'],
    'oil_temp_c':                    ['oil_reservoir_temp'],
    # a SEPARATE sensor from the reservoir (37.1 vs 38.8 degC live) — the
    # simulator only had one oil temp, so both FE fields pointed at it.
    'gear_oil_temp_c':               ['gearbox_supply_oil_temp'],
    # validator batch 2026-08-29 (#21, #28, #30): registers that exist on the
    # logger but were never surfaced.
    'oil_filter_dp_bar':             ['dp_oil_filter'],
    'elem2_dp_bar':                  ['dp_element_2'],
    'elem2_nozzle_dp_bar':           ['dp_element_2_nozzle'],
    'igv_pct':                       ['igv_pct_open'],
    'bov_pct':                       ['bov_pct_closed'],
    'cooler1_approach_c':            ['approach_temp_cooler_1'],
    'cooler2_approach_c':            ['approach_temp_cooler_2'],
    'aftercooler_approach_c':        ['aftercooler_approach'],
    'gear_box_oil_pressure_bar':     ['gearbox_oil_pressure'],
    'water_temp_c':                  ['cooling_water_in_temp'],
    'filter_dp_bar':                 ['dp_air_filter'],
    'discharge_p_bar':               ['compressor_outlet'],
    'discharge_t_c':                 ['compressor_outlet_temp'],
    'elem3_outlet_pressure_bar':     ['element_3_outlet_pressure'],
    'elem2_inlet_temp_c':            ['element_2_inlet_temp'],
    'elem3_inlet_temp_c':            ['element_3_inlet_temp'],
    'elem1_vibration_um':            ['element_1_vibration'],
    'elem2_vibration_um':            ['element_2_vibration'],
    'elem3_vibration_um':            ['element_3_vibration'],
    'lifetime_run_hours_h':          ['running_hours'],
    # validator #8 (Runtime screen): the remaining lifetime service counters.
    # `loaded_hours`/`motor_starts`/`emergency_stops` exist ONLY on air_compressor_*
    # and `module_hours` only on air_compressor_* + air_dryer_* (verified), so these
    # stay safe as global aliases.
    'loaded_hours_h':                ['loaded_hours'],
    'module_hours_h':                ['module_hours'],
    'motor_starts_n':                ['motor_starts'],
    'emergency_stops_n':             ['emergency_stops'],
    # PIN 2026-09-01 (validator #4): Loading % = the inlet-guide-vane position, the
    # machine's own capacity-control signal. NOT the requested flow/rated-flow ratio —
    # no per-compressor flow register and no rated FAD exist anywhere. Honest as a
    # load figure because the register is self-gating: IGV reads 0.0 in 100 % of
    # stopped rows and ~0 when unloaded (1.05M rows checked), 26-58 % when loaded.
    # The frame carries `loading_source` so the FE can label it "Load (IGV %)".
    'loading_pct':                   ['igv_pct_open'],
    # numeric on the logger (4 loaded / 3 unloaded / 2 stopped / 1 n-a) — the
    # consumers map it to the FE's string label, see compressor._state_label.
    'compressor_state':              ['unit_status'],
}


def _alias_expr(have, canonical):
    """SQL expression sourcing a MISSING canonical column from the table's real
    columns (per _SCHEMA_ALIAS), or None if unresolvable on this table."""
    for cand in _SCHEMA_ALIAS.get(canonical, ()):
        if isinstance(cand, str):
            if cand in have:
                return f'"{cand}"'
        else:
            agg, cols = cand
            if all(c in have for c in cols):
                parts = [f'"{c}"' for c in cols]
                if agg == 'mean':
                    return '((' + ' + '.join(parts) + f') / {len(cols)}.0)'
                if agg == 'max':
                    return 'greatest(' + ', '.join(parts) + ')'
    return None


def _parse_iso(s):
    """Parse a logger ISO string tolerantly (its fractional seconds can be 9
    digits; stdlib accepts 6) -> aware datetime. None if unparseable."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return dt.datetime.fromisoformat(re.sub(r'(\.\d{6})\d+', r'\1', s))
    except ValueError:
        return None


def _shape(rows, requested, missing, time_col):
    out = []
    for r in rows:
        d = dict(r)
        v = d.pop(time_col, None)
        # native time col comes back as datetime (.isoformat); a TEXT col is
        # already an ISO-8601 string — pass it through unchanged.
        d['ts'] = v.isoformat() if hasattr(v, 'isoformat') else (str(v) if v is not None else None)
        for m in missing:
            d[m] = None
        out.append(d)
    return out


# ── Real-logger data-correction profile (DEFERRED_MAPPING_ISSUES.md §2) ──────
# The gic "loggerfast" tables need three corrections the simulator does not.
# Verified on live data (2026-07-07):
#   1. SIGN   — instantaneous power carries the meter's CT polarity, so a source
#               / back-fed feeder reads negative (UPS-01 = -187 kW, AHU-8 = -10.8
#               kW) while a load reads positive (BPDB-01 = +367 kW). The screens
#               want the magnitude, so |value| the power columns.
#   2. ENERGY — the active/reactive counters accumulate on WHICHEVER of the
#               import/export pair matches that polarity; the other side stays 0
#               (UPS-01: active on _export=337862, _import=0; reactive on
#               _import=22523, _export=0). Real value = import + export.
#   3. RATE   — the raw rate_of_change/power_rate columns are per-(~25 s)-sample
#               noise; consumers recompute them over real Δt, so blank the raw one.
# All gated on the real DataSource DSN — the simulator path is a no-op
# (byte-identical), reusing wire_logger_ht.py's 'target_version1' marker.
_CORRECTION_ON = os.environ.get('CMD_DATA_CORRECTION', '1') not in ('0', 'false', 'False')
_REAL_DSN_MARKER = os.environ.get('CMD_REAL_DSN_MARKER', 'target_version1')

# power columns whose SIGN is CT-polarity, not true direction -> take |value|
_MAGNITUDE_POWER_COLS = {
    'active_power_total_kw', 'reactive_power_total_kvar', 'apparent_power_total_kva',
    'active_power_r_kw', 'active_power_y_kw', 'active_power_b_kw',
    'reactive_power_r_kvar', 'reactive_power_y_kvar', 'reactive_power_b_kvar',
    'apparent_power_r_kva', 'apparent_power_y_kva', 'apparent_power_b_kva',
}
# import<->export counter pairs; the real value = import + export (one side is 0)
_ENERGY_PAIRS = {
    'active_energy_import_kwh': 'active_energy_export_kwh',
    'active_energy_export_kwh': 'active_energy_import_kwh',
    'reactive_energy_import_kvarh': 'reactive_energy_export_kvarh',
    'reactive_energy_export_kvarh': 'reactive_energy_import_kvarh',
}
# raw per-sample rate columns that are noise at ~25 s cadence (recomputed by A)
_RATE_COLS = {'rate_of_change_power_kw_per_min', 'power_rate_kw_per_h'}

_REACTIVE_ENERGY_COLS = frozenset({'reactive_energy_import_kvarh', 'reactive_energy_export_kvarh'})


def _col_direction(col, direction):
    """Effective energy direction for a SPECIFIC register column (BE-59).

    A meter's seeded `energy_direction` describes its ACTIVE energy (which register
    accumulates). But an export-ACTIVE meter still IMPORTS reactive (a lagging load),
    so its reactive energy accumulates on the IMPORT register. Applying the meter's
    'export' direction to reactive picked the flat export register → wire shipped
    `reactiveEnergy: 0` while `reactive_energy_import_kvarh` climbed 212/143/30 kVArh
    (mfm 171/172/173). Resolve reactive on the import register for export meters;
    every other meter/column is unchanged."""
    if direction == 'export' and col in _REACTIVE_ENERGY_COLS:
        return 'import'
    return direction


def is_real(dsn):
    """True when ``dsn`` is the real gic 'loggerfast' logger (needs correction)."""
    return _CORRECTION_ON and bool(dsn) and _REAL_DSN_MARKER in dsn


# ── Per-meter energy direction (§2 refinement, 2026-07-07) ───────────────────
# The blind import+export sum is NOT universally right: verified against the
# avg-|power|×time integral over 7 d, UPS feeders need 'sum' (registers
# alternate with CT polarity flap; sum matches physics to <2%), BPDB-01/-05
# lamination meters need 'import' (their export register accumulates ~9× the
# physical energy — summing it inflated PCC delivered_kwh 4×, the "meter gap
# 310%" bug), transformer/HT incomers are import-only (export flat NULL/0).
# The per-meter truth lives in mfm.energy_direction / asset_meter.energy_-
# direction (seeded by the seed_energy_direction command); resolution is keyed
# by (dsn, table) — a table IS a physical meter, so duplicates agree. Unseeded
# meters default to 'sum' = the exact legacy behaviour (byte-identical).
_DIRECTIONS_LOCK = threading.Lock()
_DIRECTIONS: dict | None = None


def _load_energy_directions():
    """{(dsn, table_name): (direction, energy_scale, power_scale)} for every
    meter row that overrides the defaults. One query per process; falls back to
    {} (→ ('sum', 1.0, 1.0) everywhere) if the columns don't exist yet
    (pre-migration) or the ORM DB is unreachable."""
    out = {}
    try:
        from django.db import connection
        with connection.cursor() as cur:
            # PIN 2026-09-09: asset_meter half of the UNION dropped — no assets app in beta yet.
            cur.execute(
                "SELECT ds.dsn, m.table_name, m.energy_direction, m.energy_scale, m.power_scale "
                "FROM mfm m JOIN data_source ds ON ds.id = m.data_source_id "
                "WHERE m.table_name <> '' AND (m.energy_direction <> 'sum' OR m.energy_scale <> 1.0 OR m.power_scale <> 1.0)")
            for dsn, table, direction, escale, pscale in cur.fetchall():
                out[(dsn, table)] = (direction, float(escale or 1.0), float(pscale or 1.0))
    except Exception as exc:                       # pre-migration / metadata DB down
        log.warning('energy-direction registry unavailable (%s) — using defaults', exc)
    return out


def _energy_direction(dsn, table):
    """The meter's (direction, energy_scale, power_scale). Cached for the
    process lifetime; ``reset_energy_directions()`` after re-seeding."""
    global _DIRECTIONS
    d = _DIRECTIONS
    if d is None:
        with _DIRECTIONS_LOCK:
            d = _DIRECTIONS
            if d is None:
                d = _DIRECTIONS = _load_energy_directions()
    return d.get((dsn, table), ('sum', 1.0, 1.0))


def _power_scale(dsn, table):
    return _energy_direction(dsn, table)[2]


def reset_energy_directions():
    """Drop the cached registry so the next read reloads from the DB."""
    global _DIRECTIONS
    with _DIRECTIONS_LOCK:
        _DIRECTIONS = None


def _import_export_of(col):
    """(import_col, export_col) for either member of an energy pair."""
    pair = _ENERGY_PAIRS[col]
    return (col, pair) if '_import_' in col else (pair, col)


def _pair_value(vi, ve, direction, scale=1.0):
    """Resolve one row's (import, export) register values per the meter's
    direction, then apply its decade CT-ratio scale. None only when both
    sides are None (no data)."""
    if vi is None and ve is None:
        return None
    vi = vi or 0.0
    ve = ve or 0.0
    if direction == 'import':
        return vi * scale
    if direction == 'export':
        return ve * scale
    if direction == 'net':
        return (vi - ve) * scale
    return (vi + ve) * scale                        # 'sum' — legacy behaviour


def _augment(columns, dsn, have):
    """Requested columns + the import/export pair of any requested energy column
    (real source only), so :func:`_correct_row` can coalesce the populated side.
    The extra pair columns are dropped again by :func:`_project`."""
    if not is_real(dsn):
        return list(columns)
    extra = []
    for c in columns:
        p = _ENERGY_PAIRS.get(c)
        if p and p in have and p not in columns and p not in extra:
            extra.append(p)
    return list(columns) + extra


def _correct_row(row, dsn, dirscale=('sum', 1.0, 1.0)):
    """Apply the real-logger profile to one shaped row, in place (§2). No-op for
    the simulator / when correction is disabled. ``dirscale`` is the meter's
    (energy_direction, energy_scale, power_scale) — the direction selects which
    register (or combination) is written back to BOTH pair keys; energy_scale
    undoes a decade energy-register miswire; power_scale undoes a decade
    power-register miswire (|value| × power_scale)."""
    if row is None or not is_real(dsn):
        return row
    direction, scale, pscale = dirscale
    # BE-114 (2026-08-20, owner decision: transparency): ship the logger's SIGNED power
    # verbatim — keep only the decade power_scale. Power sign is physically meaningful
    # (import vs export, leading vs lagging VARs); abs() masked genuinely-negative
    # readings (Tf-2 reactive dips to −69 kVAr). Do NOT re-introduce abs() here.
    for c in _MAGNITUDE_POWER_COLS:
        v = row.get(c)
        if isinstance(v, (int, float)):
            row[c] = v * pscale
    seen = set()
    for a, b in _ENERGY_PAIRS.items():
        if a in seen:
            continue
        seen.add(a)
        seen.add(b)
        if a in row or b in row:
            imp, exp = _import_export_of(a)
            val = _pair_value(row.get(imp), row.get(exp), _col_direction(a, direction), scale)  # BE-59
            if a in row:
                row[a] = val
            if b in row:
                row[b] = val
    for c in _RATE_COLS:
        if c in row:
            row[c] = None
    return row


def _project(row, columns, dsn):
    """Drop the augmented pair columns the caller didn't ask for (keep 'ts')."""
    if row is None or not is_real(dsn):
        return row
    keep = set(columns)
    keep.add('ts')
    return {k: v for k, v in row.items() if k in keep}


def _energy_delta_sql(col, have, dirscale=('sum', 1.0, 1.0)):
    """Counter delta over the window, resolved per the meter's energy
    direction: the delta of the chosen register ('import'/'export'), their sum
    (legacy 'sum' — registers alternate binning on CT-flapping UPS meters), or
    the difference ('net'); multiplied by the meter's decade energy scale.
    A register absent from the table — or present but all-NULL — contributes 0."""
    direction, scale = _col_direction(col, dirscale[0]), dirscale[1]
    imp, exp = _import_export_of(col)
    # COALESCE each register delta to 0 so an all-NULL register (e.g. an unused
    # export column on an import-only meter) never poisons the sum/net to NULL —
    # which previously zeroed a feeder's whole energy while its import counter
    # was climbing (HT-01 APFCR read 0 kWh on the 'sum' path).
    d_imp = f'COALESCE(max("{imp}") - min("{imp}"), 0)' if imp in have else None
    d_exp = f'COALESCE(max("{exp}") - min("{exp}"), 0)' if exp in have else None
    if direction == 'import':
        expr = d_imp or '0'
    elif direction == 'export':
        expr = d_exp or '0'
    elif direction == 'net':
        if d_imp and d_exp:
            expr = f'({d_imp} - {d_exp})'
        elif d_imp:
            expr = d_imp
        else:
            expr = f'(0 - {d_exp})' if d_exp else '0'
    else:                                   # 'sum' — legacy blind coalesce
        if d_imp and d_exp:
            expr = f'({d_imp} + {d_exp})'
        else:
            expr = d_imp or d_exp or '0'
    return f'({expr} * {scale})' if scale != 1.0 else expr


def _metric_sql(dsn, func, col, have, dirscale=('sum', 1.0, 1.0), bools=(), tcol=None):
    """SQL for ``func(col)`` with the real-logger profile applied (§2): energy
    'delta' resolves the import/export pair per the meter's energy direction
    and decade scale; magnitude power is abs'd and × power_scale. Falls back to
    the plain aggregate for the simulator / non-power-energy columns."""
    # UINT32 service counters need their own delta (see _counter_delta_sql); the
    # column set is disjoint from the energy/power ones below, so nothing else moves.
    if func == 'delta' and tcol and col in _COUNTER_DELTA_COLS:
        ref = f'"{col}"' if (not have or col in have) else _alias_expr(have, col)
        if ref is not None:
            return _counter_delta_sql(ref, tcol)
    if is_real(dsn):
        if func == 'delta' and col in _ENERGY_PAIRS:
            return _energy_delta_sql(col, have, dirscale)
        if col in _MAGNITUDE_POWER_COLS:
            pscale = dirscale[2] if len(dirscale) > 2 else 1.0
            # avg/min/max: null the no-data sentinels BEFORE scale so a dead
            # register's 6553.5/13107 samples can't pollute the bucket.
            # BE-114: SIGNED power (no abs) — keep only the decade power_scale.
            base = _power_scrub(col) if func in ('avg', 'min', 'max') else f'"{col}"'
            ref = base if pscale == 1.0 else f'({base} * {pscale})'
            return f'(max({ref}) - min({ref}))' if func == 'delta' else f'{func}({ref})'
    # schema-alias: a canonical column absent from THIS table (e.g. the BMS
    # compressor names against a LoggerFast `air_compressor_*` table) is sourced
    # from its real column, exactly as `fetch_live` already does — otherwise the
    # live card resolves and the trend beside it is silently all-null.
    if have and col not in have:
        expr = _alias_expr(have, col)
        if expr is not None:
            return f'(max({expr}) - min({expr}))' if func == 'delta' else f'{func}({expr})'
    return _agg_expr(func, col, col in bools)


def _power_ref(dsn, col, table=None):
    """Column reference for a raw-value query: ``"col" × power_scale`` for a power
    column on the real logger (BE-114: SIGNED, no abs — keep only the decade scale),
    else the bare ``"col"``."""
    if is_real(dsn) and col in _MAGNITUDE_POWER_COLS:
        pscale = _power_scale(dsn, table) if table is not None else 1.0
        return f'"{col}"' if pscale == 1.0 else f'("{col}" * {pscale})'
    return f'"{col}"'


def fetch_live(dsn, table, columns, time_col='timestamp'):
    """Latest row for a meter table (dedicated per-meter table)."""
    _safe([table, time_col, *columns])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        want = _augment(columns, dsn, have)
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        sel = [c for c in want if c in have]
        missing = [c for c in want if c not in have]
        # schema-alias: source a missing canonical column from the table's real
        # (differently-named) columns before treating it as absent.
        alias_sel, real_missing = [], []
        for c in missing:
            expr = _alias_expr(have, c)
            (alias_sel.append(f'{expr} AS "{c}"') if expr is not None
             else real_missing.append(c))
        missing = real_missing
        cols = ', '.join([f'{tc} AS "{time_col}"', *(f'"{c}"' for c in sel), *alias_sel])
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'SELECT {cols} FROM "{table}" ORDER BY {wc} DESC LIMIT 1')
            rows = cur.fetchall()
        shaped = _shape(rows, want, missing, time_col)
        if not shaped:
            return None
        return _project(_correct_row(shaped[0], dsn, _energy_direction(dsn, table)), columns, dsn)
    finally:
        p.putconn(conn)


_AGG = re.compile(r'^(avg|min|max|sum|count|delta)$')
_BUCKETS = {'hour', 'day', 'week', 'month'}
IST = 'Asia/Kolkata'

# Logger 'no-data' sentinels (0xFFFF / 0x7FFF / 0xFFFC / 0x7FFC register families,
# incl. the ×0.1-scaled and °F→°C-converted variants, plus the per-phase-summed
# 13107 / 19660.5 / 196605 = 2/3/30 × 6553.5). CANONICAL list — the frame-level
# scrub (assets/consumers/asset_base._SENTINELS) reads this same tuple so the SQL
# aggregates and the wire scrub stay in lockstep. Matched as ±0.05 bands (float4
# storage ⇒ literal equality fails; a genuine reading never sits inside a band).
NO_DATA_SENTINELS = (65535.0, 6553.5, 655.35, 32767.0, 3276.7, 1802.6111,
                     13107.0, 19660.5, 196605.0,
                     # 0xFFFC / 0x7FFC family (Cummins pressure/temperature
                     # registers — db-probe 2026-07-30): 65532 raw, 6553.2 =
                     # ×0.1, 3276.4001 = 0x7FFC×0.1, 1802.4445 = °F→°C(3276.4)
                     65532.0, 6553.2, 3276.4001, 1802.4445)
# 0xFFFFFFFF family (air-compressor motor_winding_temp_average = 4.2949674e+08 on
# 100% of rows). float4 ulp ≈ 32 at this magnitude ⇒ needs a wide range match.
NO_DATA_SENTINEL_BIG = 4.2949674e+08


def _sentinel_free(col):
    """CASE-wrap ``"col"`` so no-data sentinel samples aggregate as NULL —
    avg/min/max skip NULLs, so one 0x7FFF sample can no longer pollute a bucket
    (e.g. the Jul-20 wind-temp 3276.7 inflating that day's avg + derived FAA).
    A no-op for real readings: only the narrow sentinel bands are nulled."""
    conds = ' OR '.join(f'"{col}" BETWEEN {s - 0.05:.4f} AND {s + 0.05:.4f}'
                        for s in NO_DATA_SENTINELS)
    big = NO_DATA_SENTINEL_BIG
    conds += f' OR "{col}" BETWEEN {big - 512.0:.1f} AND {big + 512.0:.1f}'
    return f'(CASE WHEN {conds} THEN NULL ELSE "{col}" END)'


# A single meter's raw kW/kVA/kVAr magnitude ceiling. The plant's largest unit is
# the 20 MVA HT (~20 000 kW; even a ×10 decade-scaled raw stays < 200 000). Anything
# beyond is data corruption, not a reading — e.g. 2026-07-08: 8 samples read
# 7 203 670 kW (40 020 %) on the HT, dragging that day's AVG to a fake 124 % high-load
# "warning". Bounded (not a fixed sentinel), applied ONLY to magnitude-power columns.
_POWER_MAGNITUDE_MAX = 1.0e6


def _power_scrub(col):
    """``_sentinel_free`` + the physical-impossibility ceiling above, so a corrupt
    power spike is NULLed before it pollutes an avg/min/max bucket."""
    return (f'(CASE WHEN abs("{col}") > {_POWER_MAGNITUDE_MAX:.1f} '
            f'THEN NULL ELSE {_sentinel_free(col)} END)')


# ── UINT32 lifetime counters (BMS air-compressor / air-dryer service registers) ─
# Their no-data sentinel is the FULL register max (4294967295) — three decades above
# every band in NO_DATA_SENTINELS — so a plain max()-min() over a bucket holding one
# reads ~4.3e9 instead of a few hours (air_dryer_1.running_hours: 4 of 13 Aug days).
# Delta for these is LAST - FIRST over the sentinel-scrubbed samples, so a controller
# counter RESET reads negative instead of silently inflating the window.
_UINT32_COUNTER_SENTINEL = 4.0e9
_COUNTER_DELTA_COLS = frozenset({
    # canonical names the consumers ask for …
    'lifetime_run_hours_h', 'loaded_hours_h', 'module_hours_h',
    'motor_starts_n', 'emergency_stops_n',
    # … and the logger's own spellings, for a table that has them directly.
    'running_hours', 'loaded_hours', 'module_hours', 'motor_starts', 'emergency_stops',
})


def _counter_delta_sql(ref, tc):
    """Sentinel-safe, reset-safe counter delta over a bucket: register-max samples
    NULLed, then last - first (ordered by time via the ARRAY[epoch, value] trick,
    which max()/min() compare lexicographically in one pass).
    A NEGATIVE result means the counter was reset mid-bucket -> NULL, not 0: the
    reset hides an unknown amount of runtime, and a 0 would draw as a real idle hour."""
    scrub = f'(CASE WHEN {ref} >= {_UINT32_COUNTER_SENTINEL:.1f} THEN NULL ELSE {ref} END)'
    key = f'ARRAY[extract(epoch from {tc}), {scrub}]'
    fltr = f'FILTER (WHERE {scrub} IS NOT NULL)'
    d = f'((max({key}) {fltr})[2] - (min({key}) {fltr})[2])'
    return f'(CASE WHEN {d} < 0 THEN NULL ELSE {d} END)'


def _agg_expr(func, col, is_bool=False):
    # A BOOLEAN column cannot be averaged/summed in Postgres. The BMS VFD blocks
    # (e.g. `general_air_exhaust_unit.vfd_N_on_fb`) are genuine booleans, and
    # avg()/sum() over them is exactly what a "how many blowers were on this
    # bucket" series needs — so cast to int first. Sentinel-scrubbing is skipped:
    # a boolean has no register-max sentinel.
    if is_bool:
        ref = f'("{col}")::int'
        if func == 'delta':
            return f'max({ref}) - min({ref})'
        return f'{func}({ref})'
    if func == 'delta':                       # counter delta over the window
        return f'max("{col}") - min("{col}")'
    if func in ('avg', 'min', 'max'):         # sentinels must not pollute buckets
        return f'{func}({_sentinel_free(col)})'
    return f'{func}("{col}")'


def fetch_bucketed(dsn, table, start, end, bucket, metrics, time_col='timestamp'):
    """Aggregate per time bucket (IST-aligned) over [start, end).

    metrics: {alias: (func, column)} with func in avg|min|max|sum|count|delta.
    bucket : 'hour'|'day'|'week'|'month'  → one row per bucket;
             None                          → one aggregate row over the range.
    """
    _safe([table, time_col])
    if bucket is not None and bucket not in _BUCKETS:
        raise ValueError(f'bad bucket: {bucket}')
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        bools = _bool_cols(conn, dsn, table)
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        direction = _energy_direction(dsn, table)
        sel = []
        for alias, (func, col) in metrics.items():
            if not (_IDENT.match(alias) and _AGG.match(func) and _IDENT.match(col)):
                raise ValueError(f'bad metric: {alias}/{func}/{col}')
            # present, or resolvable through the schema-alias table (_metric_sql
            # builds the aliased expression); anything else is genuinely absent
            # and is simply omitted, leaving the FE a null series.
            if col in have or _alias_expr(have, col) is not None:
                sel.append(f'{_metric_sql(dsn, func, col, have, direction, bools, tc)} AS "{alias}"')
        if not sel:
            return []
        if bucket:
            bexpr = f"date_trunc('{bucket}', ({tc} AT TIME ZONE '{IST}'))"
            sql = (f'SELECT {bexpr} AS bucket, {", ".join(sel)} FROM "{table}" '
                   f'WHERE {wc} >= %s AND {wc} < %s GROUP BY bucket ORDER BY bucket')
        else:
            sql = (f'SELECT NULL::timestamp AS bucket, {", ".join(sel)} FROM "{table}" '
                   f'WHERE {wc} >= %s AND {wc} < %s')
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (bnd(start), bnd(end)))
            rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            b = d.pop('bucket')
            d['bucket'] = b.isoformat() if b else None
            out.append(d)
        return out
    finally:
        p.putconn(conn)


def fetch_filtered_avg(dsn, tables, start, end, col, gt=0.0, time_col='timestamp'):
    """AVG(col) over [start, end) restricted to rows where col > gt — a
    'running average' (e.g. engine speed while running, excluding the off-state 0s
    that smear a plain bucket average). Tries each table; uses the first that has
    the column. Returns a float or None."""
    if not _IDENT.match(col):
        raise ValueError(f'bad column: {col}')
    for table in (tables if isinstance(tables, (list, tuple)) else [tables]):
        _safe([table, time_col])
        p = _pool(dsn)
        conn = p.getconn()
        try:
            if col not in _columns(conn, dsn, table):
                continue
            _tc, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
            sql = (f'SELECT AVG("{col}") AS v FROM "{table}" '
                   f'WHERE {wc} >= %s AND {wc} < %s AND "{col}" > %s')
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (bnd(start), bnd(end), gt))
                r = cur.fetchone()
            return float(r['v']) if r and r['v'] is not None else None
        finally:
            p.putconn(conn)
    return None


def fetch_event_counts(dsn, table, start, end, bucket, flags, time_col='timestamp'):
    """Count rising edges (false→true) of boolean flags per IST bucket.

    bucket None → single row of totals over the range.
    """
    _safe([table, time_col, *flags])
    if bucket is not None and bucket not in _BUCKETS:
        raise ValueError(f'bad bucket: {bucket}')
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        boolset = _bool_cols(conn, dsn, table)
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        flags = [f for f in flags if f in have]
        if not flags:
            return []

        # Real loggers type *_event_active as real (0.0/1.0), not boolean, so
        # `"f" AND ...` errors. Coerce non-boolean flags to truthy (`f <> 0`).
        def _now(f):
            return f'"{f}"' if f in boolset else f'("{f}" <> 0)'

        def _prev(f):
            return f'coalesce(prev_{f}, false)' if f in boolset else f'(coalesce(prev_{f}, 0) <> 0)'

        lags = ', '.join(f'lag("{f}") OVER (ORDER BY {wc}) AS prev_{f}' for f in flags)
        cnts = ', '.join(f'count(*) FILTER (WHERE {_now(f)} AND NOT {_prev(f)}) AS "{f}"' for f in flags)
        cols = ', '.join(f'"{f}"' for f in flags)
        bexpr = (f"date_trunc('{bucket}', ({time_col} AT TIME ZONE '{IST}'))" if bucket else 'NULL::timestamp')
        grp = 'GROUP BY bucket ORDER BY bucket' if bucket else ''
        sql = (f'SELECT {bexpr} AS bucket, {cnts} FROM '
               f'(SELECT {tc} AS "{time_col}", {cols}, {lags} FROM "{table}" '
               f'WHERE {wc} >= %s AND {wc} < %s) s {grp}')
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (bnd(start), bnd(end)))
            rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            b = d.pop('bucket')
            d['bucket'] = b.isoformat() if b else None
            out.append(d)
        return out
    finally:
        p.putconn(conn)


def fetch_threshold_events(dsn, table, start, end, bucket, specs, time_col='timestamp'):
    """Count rising-edge threshold crossings of NUMERIC columns per IST bucket.

    The simulator's boolean ``*_event_active`` flags never fire, but the raw
    deviation/unbalance columns do cross statutory limits — so detect events
    directly (P0 #7). ``specs``: {alias: (column, 'below'|'above', threshold)}.
    An event = a sample entering the violation region from outside it.
    ``bucket`` None → single row of totals over the range.
    """
    _safe([table, time_col])
    if bucket is not None and bucket not in _BUCKETS:
        raise ValueError(f'bad bucket: {bucket}')
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        valid = {}
        for alias, (col, direction, thr) in specs.items():
            if not (_IDENT.match(alias) and _IDENT.match(col)) or direction not in ('below', 'above'):
                raise ValueError(f'bad event spec: {alias}/{col}/{direction}')
            if col in have:
                valid[alias] = (col, direction, float(thr))
        if not valid:
            return []
        cols = sorted({c for c, _d, _t in valid.values()})
        # lag() over ONLY the window makes prev NULL on the window's first row,
        # which manufactures a phantom edge whenever the asset is already inside
        # the violation region at range start (e.g. a genset stopped all day
        # scored stopsToday=1 with zero real transitions). Seed the lag with the
        # last sample BEFORE ``start``: it participates in lag() ordering only,
        # then the outer ``NOT _seed`` filter trims it so it is never counted.
        lags = ', '.join(f'lag("{c}") OVER (ORDER BY "{time_col}") AS prev_{c}' for c in cols)
        cnts = []
        for alias, (col, direction, thr) in valid.items():
            if direction == 'below':
                cnts.append(f'count(*) FILTER (WHERE "{col}" < {thr} AND '
                            f'(prev_{col} IS NULL OR prev_{col} >= {thr})) AS "{alias}"')
            else:
                cnts.append(f'count(*) FILTER (WHERE "{col}" > {thr} AND '
                            f'(prev_{col} IS NULL OR prev_{col} <= {thr})) AS "{alias}"')
        selcols = ', '.join(f'"{c}"' for c in cols)
        bexpr = (f"date_trunc('{bucket}', ({time_col} AT TIME ZONE '{IST}'))" if bucket else 'NULL::timestamp')
        grp = 'GROUP BY bucket ORDER BY bucket' if bucket else ''
        sql = (f'SELECT {bexpr} AS bucket, {", ".join(cnts)} FROM '
               f'(SELECT "{time_col}", {selcols}, _seed, {lags} FROM '
               f'((SELECT {tc} AS "{time_col}", {selcols}, false AS _seed FROM "{table}" '
               f'WHERE {wc} >= %s AND {wc} < %s) '
               f'UNION ALL '
               f'(SELECT {tc} AS "{time_col}", {selcols}, true AS _seed FROM "{table}" '
               f'WHERE {wc} < %s ORDER BY {wc} DESC LIMIT 1)) w) s '
               f'WHERE NOT _seed {grp}')
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (bnd(start), bnd(end), bnd(start)))
            rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            b = d.pop('bucket')
            d['bucket'] = b.isoformat() if b else None
            out.append(d)
        return out
    finally:
        p.putconn(conn)


def fetch_state_seconds(dsn, table, start, end, column, threshold=0.5, max_gap_s=120.0,
                        time_col='timestamp'):
    """Total seconds within [start, end) where numeric ``column`` > ``threshold``
    (e.g. UPS time-on-battery from ``ups_mode`` — B3.5; no logger accumulator
    column exists). Dwell attribution: each qualifying sample contributes the
    gap to the NEXT sample, capped at ``max_gap_s`` so a logger outage that
    starts mid-state cannot inflate the span; the window's last sample
    contributes 0 (conservative). Returns float seconds — 0.0 when rows exist
    but never cross the threshold (evidence of absence), or None when the
    column is absent on ``table`` (honest-null: absence of evidence)."""
    _safe([table, time_col, column])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        if column not in _columns(conn, dsn, table):
            return None
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        # order by the projected CAST time (chronological even where the text
        # column mixes +00:00/+05:30 offsets); WHERE stays on the raw column so
        # the btree index is usable — same split fetch_threshold_events uses.
        sql = (f'SELECT coalesce(sum(least(s.gap, {float(max_gap_s)})), 0.0) FROM '
               f'(SELECT extract(epoch FROM (lead(w.t) OVER (ORDER BY w.t) - w.t)) AS gap, '
               f'w.v FROM (SELECT {tc} AS t, "{column}" AS v FROM "{table}" '
               f'WHERE {wc} >= %s AND {wc} < %s) w) s '
               f'WHERE s.v > {float(threshold)} AND s.gap IS NOT NULL')
        with conn.cursor() as cur:
            cur.execute(sql, (bnd(start), bnd(end)))
            row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    finally:
        p.putconn(conn)


# ── Range/sampling hybrid filter (ported from the old backend) ──────────────
# Sampling tokens → IST-aligned bucket SQL. date_trunc for calendar units;
# epoch-floor for sub-day windows (3h 'hourly', 2h, 8h 'shift'). Bucket edges
# align to IST; the wire value stays a UTC-aware TIMESTAMPTZ.
_SAMPLING_SECONDS = {'5min': 300, '15min': 900, '30min': 1800,
                     'hourly': 3 * 3600, '2hour': 2 * 3600, 'shift': 8 * 3600}
VALID_SAMPLINGS = {'minute', 'hour', 'day', 'week', 'month',
                   '5min', '15min', '30min', 'hourly', '2hour', 'shift'}
# IST shifts (engineer spec): A 06:00-14:00, B 14:00-22:00, C 22:00-06:00 — C spans
# midnight. The day's first shift starts at 06:00, so shift buckets floor 8h windows
# from a 06:00 anchor (see _shift_start and the offset in _sampling_expr), NOT from
# midnight. SHIFT_LABELS is keyed by the shift-start IST hour.
_SHIFT_ANCHOR_HOUR = 6
_SHIFT_OFFSET_SECONDS = _SHIFT_ANCHOR_HOUR * 3600
SHIFT_LABELS = {6: 'A', 14: 'B', 22: 'C'}


def _sampling_expr(sampling, tc):
    tz = f"'{IST}'"
    if sampling in ('minute', 'hour', 'day', 'week', 'month'):
        return f"(date_trunc('{sampling}', ({tc} AT TIME ZONE {tz})) AT TIME ZONE {tz})"
    if sampling in _SAMPLING_SECONDS:
        s = _SAMPLING_SECONDS[sampling]
        # 'shift' floors from a 06:00 IST anchor (A 06-14 / B 14-22 / C 22-06); other
        # sub-day samplings floor from midnight (offset 0).
        off = _SHIFT_OFFSET_SECONDS if sampling == 'shift' else 0
        return (f"((to_timestamp((floor((extract(epoch from {tc} AT TIME ZONE {tz}) - {off}) "
                f"/ {s}) * {s}) + {off}) AT TIME ZONE 'UTC') AT TIME ZONE {tz})")
    raise ValueError(f'bad sampling: {sampling}')


def fetch_sampled_mode(dsn, table, start, end, sampling, column, time_col='timestamp'):
    """Modal (most-common) value of a CATEGORICAL ``column`` per sampling bucket
    — for a State band like UPS ``operating_state`` (Normal/Bypass/On Battery)
    that can't be averaged. Returns ``[{bucket, mode}]`` IST-bucketed.
    """
    _safe([table, time_col, column])
    if sampling not in VALID_SAMPLINGS:
        raise ValueError(f'bad sampling: {sampling}')
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        # the state column may be a canonical name the real logger spells
        # differently (BMS `compressor_state` -> `unit_status`)
        cexpr = f'"{column}"' if column in have else _alias_expr(have, column)
        if cexpr is None:
            return []
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        bexpr = _sampling_expr(sampling, tc)
        sql = (f'SELECT {bexpr} AS bucket, '
               f'mode() WITHIN GROUP (ORDER BY {cexpr}) AS mode FROM "{table}" '
               f'WHERE {wc} >= %s AND {wc} < %s GROUP BY bucket ORDER BY bucket')
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (bnd(start), bnd(end)))
            rows = cur.fetchall()
        out = []
        for r in rows:
            b = r['bucket']
            out.append({'bucket': b.isoformat() if b else None, 'mode': r['mode']})
        return out
    finally:
        p.putconn(conn)


def fetch_sampled(dsn, table, start, end, sampling, metrics, time_col='timestamp'):
    """Like fetch_bucketed but buckets by a sampling token (hourly=3h, 2hour,
    shift=8h, day, week, month), IST-aligned. metrics: {alias: (func, column)}.
    """
    _safe([table, time_col])
    if sampling not in VALID_SAMPLINGS:
        raise ValueError(f'bad sampling: {sampling}')
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        bools = _bool_cols(conn, dsn, table)
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        direction = _energy_direction(dsn, table)
        sel = []
        for alias, (func, col) in metrics.items():
            if not (_IDENT.match(alias) and _AGG.match(func) and _IDENT.match(col)):
                raise ValueError(f'bad metric: {alias}/{func}/{col}')
            # present, or resolvable through the schema-alias table (_metric_sql
            # builds the aliased expression); anything else is genuinely absent
            # and is simply omitted, leaving the FE a null series.
            if col in have or _alias_expr(have, col) is not None:
                sel.append(f'{_metric_sql(dsn, func, col, have, direction, bools, tc)} AS "{alias}"')
        if not sel:
            return []
        bexpr = _sampling_expr(sampling, tc)
        sql = (f'SELECT {bexpr} AS bucket, {", ".join(sel)} FROM "{table}" '
               f'WHERE {wc} >= %s AND {wc} < %s GROUP BY bucket ORDER BY bucket')
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (bnd(start), bnd(end)))
            rows = cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            b = d.pop('bucket')
            d['bucket'] = b.isoformat() if b else None
            out.append(d)
        return out
    finally:
        p.putconn(conn)


def hour_of_day_profile(dsn, table, col, start, end, time_col='timestamp', exclude_idle=False):
    """Per-hour-of-day MEDIAN + p10–p90 half-band of ``col`` over [start, end),
    IST wall-clock. Returns ``{hour(0-23): (centre, half_band)}`` — the trailing
    "expected" profile for a load-anomalies band. Uses median/percentiles (NOT
    avg/stddev_pop) so a single glitch sample can't poison an hour's centre or
    spread. Empty dict when the column is absent or the table has no rows.

    ``exclude_idle`` (LOAD/POWER columns only): drop idle/off readings — values
    ``<= 10%`` of the window's p90 — BEFORE the percentiles, so an idle-prone feeder's
    band reflects its RUNNING load, not the 0-floor. Without it, a feeder that sits at
    ~0 for >10% of the window has p10=0, which inflates ``(p90-p10)/2`` to ~p90/2 and
    the "expected range" spans the whole chart (BE-39). Scale-free (relative to p90)."""
    _safe([table, time_col, col])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        if col not in _columns(conn, dsn, table):
            return {}
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        pref = _power_ref(dsn, col, table)   # |value| × power_scale for a CT-polarity power col (§2)
        idle = ''
        if exclude_idle:
            idle = (f'AND {pref} > 0.10 * (SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY {pref}) '
                    f'FROM "{table}" WHERE {wc} >= %s AND {wc} < %s) ')
        sql = (f"SELECT extract(hour FROM ({tc} AT TIME ZONE '{IST}'))::int AS h, "
               f'percentile_cont(0.5) WITHIN GROUP (ORDER BY {pref}) AS med, '
               f'(percentile_cont(0.9) WITHIN GROUP (ORDER BY {pref}) '
               f'- percentile_cont(0.1) WITHIN GROUP (ORDER BY {pref})) / 2.0 AS halfband '
               f'FROM "{table}" WHERE {wc} >= %s AND {wc} < %s {idle}GROUP BY h ORDER BY h')
        params = ((bnd(start), bnd(end), bnd(start), bnd(end)) if exclude_idle
                  else (bnd(start), bnd(end)))
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        out = {}
        for h, med, hb in rows:
            if h is not None and med is not None:
                out[int(h)] = (float(med), float(hb) if hb is not None else 0.0)
        return out
    finally:
        p.putconn(conn)


def counter_at(dsn, table, col, instant, time_col='timestamp', until=None):
    """Value of a monotonic counter ``col`` at the latest row strictly BEFORE
    ``instant`` — the carry-forward baseline for a since-anchor delta
    (``today = counter(now) - counter(midnight)``). Falls back to the earliest
    row AT/after the instant when the table has no row before it (data that
    starts today). None if the column is absent or the table is empty.

    Rows whose counter registers are ALL NULL are SKIPPED — a NULL row is not a
    zero reading. Coalescing one to 0 made a since-anchor delta return the whole
    lifetime counter (BE-53: baseline 0 → ``top - 0``); e.g. mfm 13/24 have NULL
    counters 06-27→07-07, so a `this_month` anchor landed on a NULL row and the
    Cumulative Energy card showed 4,829 / 9,207 MWh instead of the real MTD.

    ``until`` bounds the forward fallback to the caller's window end, so a
    baseline can never be taken from a reading OUTSIDE the selected range."""
    _safe([table, time_col, col])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        if col in have:
            base_expr = f'"{col}"'
        else:                                   # schema-alias (e.g. apparent_energy_kvah
            base_expr = _alias_expr(have, col)  # → apparent_energy_delivered_kvah)
            if base_expr is None:
                return None
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        # real logger: resolve the import/export pair per the meter's energy
        # direction (§2) — sum for CT-flapping meters, a single register when
        # the twin is garbage/flat, net for true bidirectional accounting.
        expr = base_expr
        # "this row carries a real reading" guard (BE-53). The coalesce below is
        # still correct for merging a NULL *twin* register, but a row where EVERY
        # register is NULL must be skipped, not read as 0.
        nn = f'(({base_expr}) IS NOT NULL)'
        if is_real(dsn) and col in _ENERGY_PAIRS:
            imp, exp = _import_export_of(col)
            i_ref = f'coalesce("{imp}", 0)' if imp in have else '0'
            e_ref = f'coalesce("{exp}", 0)' if exp in have else '0'
            _present = [c for c in (imp, exp) if c in have]
            if _present:                      # real reading = at least ONE register populated
                nn = '(' + ' OR '.join(f'"{c}" IS NOT NULL' for c in _present) + ')'
            direction, scale, _pscale = _energy_direction(dsn, table)
            direction = _col_direction(col, direction)          # BE-59: reactive on import for export meters
            expr = {'import': i_ref,
                    'export': e_ref,
                    'net':    f'({i_ref} - {e_ref})'}.get(direction,
                              f'({i_ref} + {e_ref})')
            if scale != 1.0:
                expr = f'(({expr}) * {scale})'
        with conn.cursor() as cur:
            cur.execute(f'SELECT {expr} FROM "{table}" WHERE {wc} < %s AND {nn} '
                        f'ORDER BY {wc} DESC LIMIT 1', (bnd(instant),))
            r = cur.fetchone()
            if r is None:      # no real reading before the anchor — use the first one AT/after
                if until is not None:          # …bounded to the window: a baseline must never
                    cur.execute(                # come from a reading OUTSIDE the selected range
                        f'SELECT {expr} FROM "{table}" WHERE {wc} >= %s AND {wc} < %s AND {nn} '
                        f'ORDER BY {wc} ASC LIMIT 1', (bnd(instant), bnd(until)))
                else:
                    cur.execute(f'SELECT {expr} FROM "{table}" WHERE {wc} >= %s AND {nn} '
                                f'ORDER BY {wc} ASC LIMIT 1', (bnd(instant),))
                r = cur.fetchone()
        return float(r[0]) if r and r[0] is not None else None
    finally:
        p.putconn(conn)


def peak_with_time(dsn, table, col, start, end, time_col='timestamp'):
    """(max value, ISO timestamp of that max) for ``col`` over [start, end) —
    backend-computed peak-demand-today + its time. (None, None) if absent/empty."""
    _safe([table, time_col, col])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        if col not in _columns(conn, dsn, table):
            return None, None
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        pref = _power_ref(dsn, col, table)   # |value| × power_scale for a CT-polarity power col (§2)
        with conn.cursor() as cur:
            cur.execute(f'SELECT {pref}, {tc} FROM "{table}" '
                        f'WHERE {wc} >= %s AND {wc} < %s AND "{col}" IS NOT NULL '
                        f'ORDER BY {pref} DESC, {wc} DESC LIMIT 1', (bnd(start), bnd(end)))
            r = cur.fetchone()
        if not r or r[0] is None:
            return None, None
        return float(r[0]), (r[1].isoformat() if hasattr(r[1], 'isoformat') else (str(r[1]) if r[1] else None))
    finally:
        p.putconn(conn)


def demand_window(dsn, table, col, block_min, start, end, time_col='timestamp'):
    """(present demand, max demand) over [start, end) using fixed demand blocks.
    present = avg(col) over the last ``block_min`` minutes; max = the largest
    per-block average across the window (standard utility MD integration)."""
    _safe([table, time_col, col])
    secs = int(block_min) * 60
    p = _pool(dsn)
    conn = p.getconn()
    try:
        if col not in _columns(conn, dsn, table):
            return None, None
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        pref = _power_ref(dsn, col, table)   # |value| × power_scale for a CT-polarity power col (§2)
        block_start = end - dt.timedelta(minutes=int(block_min))   # interval done in Python (index-safe)
        with conn.cursor() as cur:
            cur.execute(f'SELECT avg({pref}) FROM "{table}" '
                        f'WHERE {wc} >= %s AND {wc} < %s', (bnd(block_start), bnd(end)))
            present = cur.fetchone()[0]
            bexpr = f'floor(extract(epoch from {tc}) / {secs})'
            cur.execute(f'SELECT max(b) FROM (SELECT avg({pref}) b FROM "{table}" '
                        f'WHERE {wc} >= %s AND {wc} < %s GROUP BY {bexpr}) s',
                        (bnd(start), bnd(end)))
            dmax = cur.fetchone()[0]
        return (float(present) if present is not None else None,
                float(dmax) if dmax is not None else None)
    finally:
        p.putconn(conn)


def resolve_range(preset, now, start=None, end=None):
    """Resolve a named range preset to (start, end) UTC, IST-anchored.

    Presets mirror the old backend's history dispatcher. `now` anchors the
    window (we pass the data's latest timestamp so historical DBs resolve to
    where the data actually is). ``preset='custom'`` honors the ``start``/``end``
    date selection (falls back to today if they're missing/unparseable).
    """
    now_ist = now.astimezone(dt.timezone.utc).astimezone(_IST_TZ)
    mid = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    p = (preset or 'today').lower().replace('-', '_')

    def u(x):
        return x.astimezone(dt.timezone.utc)

    # accept every custom spelling the FE sends: 'custom' (older controls), plus the
    # event-strip presets 'custom_range' / 'custom_date' (and their hyphen forms, already
    # normalized to '_' above). All resolve from the start/end window.
    if p in ('custom', 'custom_range', 'custom_date'):
        cs, ce = custom_window(start, end)
        if cs is not None:
            return cs, ce
        # unparseable custom -> fall through to today
    if p == 'today':
        return u(mid), now
    if p == 'yesterday':
        return u(mid - dt.timedelta(days=1)), u(mid)
    if p in ('this_week', 'thisweek'):
        return u(mid - dt.timedelta(days=now_ist.weekday())), now
    if p in ('last_week', 'last_7d', 'last7d'):
        return u(mid - dt.timedelta(days=7)), now
    if p in ('this_month', 'thismonth'):
        return u(mid.replace(day=1)), now
    if p in ('last_month', 'lastmonth'):
        this_first = mid.replace(day=1)
        prev_first = (this_first - dt.timedelta(days=1)).replace(day=1)
        return u(prev_first), u(this_first)
    if p in ('last_30d', 'last30d'):
        return u(mid - dt.timedelta(days=30)), now
    if p in ('last_24h', 'last24h'):
        return now - dt.timedelta(hours=24), now
    return u(mid), now            # default → today


# Canonical range tokens resolve_range resolves to a real window. BE-47: an unknown
# token silently resolves to 'today'; a consumer that echoes the raw token then lies
# ("range: last_7_days" while it is actually showing today's data). normalize_range
# collapses each spelling variant to its canonical token and maps anything unknown to
# 'today', so a consumer can echo the range ACTUALLY used instead of the stray token.
_RANGE_ALIASES = {
    'thisweek': 'this_week', 'lastweek': 'last_week',
    'last7d': 'last_7d', 'last_7days': 'last_7d', 'last_7_days': 'last_7d',
    'thismonth': 'this_month', 'lastmonth': 'last_month',
    'last30d': 'last_30d', 'last24h': 'last_24h',
    'custom_range': 'custom', 'custom_date': 'custom',   # FE event-strip presets → custom window
}
KNOWN_RANGES = frozenset({
    'custom', 'today', 'yesterday', 'this_week', 'last_week', 'last_7d',
    'this_month', 'last_month', 'last_30d', 'last_24h',
})


def normalize_range(preset):
    """Canonical range token for ``preset``, or 'today' if unrecognized (BE-47).
    Mirrors resolve_range's own token acceptance so the returned token resolves to
    the same window — lets a consumer echo the range it ACTUALLY used instead of a
    stray token that silently fell back to today."""
    p = (preset or 'today').lower().replace('-', '_')
    p = _RANGE_ALIASES.get(p, p)
    return p if p in KNOWN_RANGES else 'today'


_IST_TZ = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _shift_start(d):
    """IST datetime at which ``d``'s 8h shift began — shifts start at 06:00, 14:00,
    22:00 IST (A/B/C). Shift C (22:00-06:00) spans midnight, so 00:00-05:59 maps back
    to 22:00 the PREVIOUS day. One source of truth so every shift bucketer agrees."""
    d = d.astimezone(_IST_TZ) if d.tzinfo else d.replace(tzinfo=_IST_TZ)
    anchor = d.replace(hour=_SHIFT_ANCHOR_HOUR, minute=0, second=0, microsecond=0)
    if d < anchor:                       # before 06:00 → previous day's 06:00 anchor
        anchor -= dt.timedelta(days=1)
    hours = int((d - anchor).total_seconds() // 3600)
    return anchor + dt.timedelta(hours=(hours // 8) * 8)


def _parse_ist_date(s):
    """Parse an ISO date ('YYYY-MM-DD') or datetime string as an IST instant
    (a bare date -> IST midnight). None if unparseable."""
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        try:
            d = dt.datetime.combine(dt.date.fromisoformat(s), dt.time())
        except ValueError:
            return None
    return d.replace(tzinfo=_IST_TZ) if d.tzinfo is None else d


def custom_window(start, end):
    """Resolve a custom selection to a UTC window. Tolerates reversed bounds;
    ``(None, None)`` if either bound is unparseable/missing.

    - Bare dates ('YYYY-MM-DD') -> ``[start 00:00 IST, (end+1d) 00:00 IST)`` — the
      end day is fully included (unchanged).
    - Hour-precise ('YYYY-MM-DDTHH:MM', either bound carrying a time) -> the EXACT
      ``[start, end)`` instants, no day-snapping — e.g. 08:00 → 16:00 of a day.
    """
    s = _parse_ist_date(start)
    e = _parse_ist_date(end)
    if s is None or e is None:
        return None, None
    s = s.astimezone(_IST_TZ)
    e = e.astimezone(_IST_TZ)
    timed = any(('T' in b or ':' in b) for b in (start, end) if isinstance(b, str))
    if timed:                                       # exact instants (bare-date bound -> IST midnight)
        lo, hi = (s, e) if s <= e else (e, s)
        return lo.astimezone(dt.timezone.utc), hi.astimezone(dt.timezone.utc)
    s = s.replace(hour=0, minute=0, second=0, microsecond=0)
    e = e.replace(hour=0, minute=0, second=0, microsecond=0)
    lo, hi = (s, e) if s <= e else (e, s)
    return lo.astimezone(dt.timezone.utc), (hi + dt.timedelta(days=1)).astimezone(dt.timezone.utc)


def window_bounds(qs):
    """Extract (start, end) ISO strings from a WS query-string dict, tolerant of both
    naming conventions the FE uses: the panel event-strip contract ships
    ``start_date`` / ``end_date`` (eventFilterRules.backendParams), while older
    history controls ship ``start`` / ``end``. Missing → None. Pair with
    ``resolve_range(range, now, start, end)`` for custom-window support."""
    start = (qs.get('start_date') or qs.get('start') or [None])[0]
    end = (qs.get('end_date') or qs.get('end') or [None])[0]
    return start, end


def custom_samplings(start, end):
    """Samplings offered for a custom ``[start, end)`` window, by day-span — keeps
    the bucket count bounded (no hour-sampling a month). None bounds -> ()."""
    if start is None or end is None:
        return ()
    days = max(1, round((end - start).total_seconds() / 86400))
    if days <= 2:
        return ('hour', 'hourly', 'shift')
    if days <= 45:
        return ('day',)
    if days <= 370:
        return ('week',)
    return ('month',)


def range_full(preset, now, start=None, end=None):
    """Canonical [start, full_end] for a preset — the FULL period (today = a
    whole 24 h day, this_month = the whole month), NOT anchored to ``now``. Used
    to build a fixed-length bucket grid so history arrays return the exact count
    the FE expects even when today is only partly elapsed. ``custom`` returns the
    selected window verbatim (it's already fully specified)."""
    now_ist = now.astimezone(_IST_TZ)
    mid = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    p = (preset or 'today').lower().replace('-', '_')

    def u(x):
        return x.astimezone(dt.timezone.utc)
    day = dt.timedelta(days=1)
    if p == 'custom':
        cs, ce = custom_window(start, end)
        if cs is not None:
            return cs, ce
    if p == 'yesterday':
        return u(mid - day), u(mid)
    if p in ('this_week', 'thisweek'):
        ws = mid - dt.timedelta(days=now_ist.weekday())
        return u(ws), u(ws + dt.timedelta(days=7))
    if p in ('last_week', 'last_7d', 'last7d'):
        return u(mid - dt.timedelta(days=7)), u(mid + day)
    if p in ('this_month', 'thismonth'):
        ms = mid.replace(day=1)
        nm = (ms + dt.timedelta(days=32)).replace(day=1)
        return u(ms), u(nm)
    if p in ('last_month', 'lastmonth'):
        ms = mid.replace(day=1)
        pm = (ms - dt.timedelta(days=1)).replace(day=1)
        return u(pm), u(ms)
    if p in ('last_30d', 'last30d'):
        return u(mid - dt.timedelta(days=30)), u(mid + day)
    return u(mid), u(mid + day)                 # today (default)


def _add_month(d):
    return (d.replace(day=1) + dt.timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)


def bucket_grid(start, full_end, sampling):
    """IST-aligned bucket-start datetimes over [start, full_end), matching
    :func:`fetch_sampled`'s bucketing (IST wall-clock aligned). Returns IST
    datetimes; ``.isoformat()`` matches the ``bucket`` key fetch_sampled emits."""
    s = start.astimezone(_IST_TZ)
    e = full_end.astimezone(_IST_TZ)
    out = []
    if sampling == 'shift':                         # 8h shifts anchored at 06:00 IST
        cur, step = _shift_start(s), dt.timedelta(hours=8)
    elif sampling in _SAMPLING_SECONDS:             # 3h/2h — align to IST midnight
        step = dt.timedelta(seconds=_SAMPLING_SECONDS[sampling])
        midnight = s.replace(hour=0, minute=0, second=0, microsecond=0)
        n = int((s - midnight).total_seconds() // _SAMPLING_SECONDS[sampling])
        cur = midnight + n * step
    elif sampling == 'hour':
        cur, step = s.replace(minute=0, second=0, microsecond=0), dt.timedelta(hours=1)
    elif sampling == 'day':
        cur, step = s.replace(hour=0, minute=0, second=0, microsecond=0), dt.timedelta(days=1)
    elif sampling == 'week':
        cur = s.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=s.weekday())
        step = dt.timedelta(weeks=1)
    elif sampling == 'month':
        cur, step = s.replace(day=1, hour=0, minute=0, second=0, microsecond=0), None
    else:
        return out
    while cur < e:
        out.append(cur)
        cur = _add_month(cur) if sampling == 'month' else cur + step
    return out


def bucket_window(iso, sampling):
    """(bucket_start_iso, bucket_end_iso) for a bucket, given its sampling.

    Multi-day ranges need an unambiguous per-bucket timestamp — a bare "00:00"
    can't say which day. Naive bucket isoformats (from fetch_bucketed) are
    assumed IST; end = start + the sampling step."""
    if not iso:
        return None, None
    try:
        start = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso, None
    if start.tzinfo is None:
        start = start.replace(tzinfo=_IST_TZ)
    if sampling in _SAMPLING_SECONDS:
        end = start + dt.timedelta(seconds=_SAMPLING_SECONDS[sampling])
    elif sampling == 'minute':
        end = start + dt.timedelta(minutes=1)
    elif sampling == 'hour':
        end = start + dt.timedelta(hours=1)
    elif sampling == 'day':
        end = start + dt.timedelta(days=1)
    elif sampling == 'week':
        end = start + dt.timedelta(weeks=1)
    elif sampling == 'month':
        end = _add_month(start)
    else:
        end = None
    return start.isoformat(), (end.isoformat() if end else None)


def bucket_label(iso, sampling, multiday=False):
    """Short label for a bucket: 'date + shift' for shift, time-of-day for other
    sub-day samplings, DATE for day/week/month (a bare '00:00' can't identify the
    day on a multi-day range).

    ``multiday=True`` (BE-48): the caller knows this intraday sampling spans more
    than one day (e.g. a custom 3-day window at 2-hour buckets), so prepend the
    date — otherwise each day's clock repeats and the ticks read 'backwards'. The
    default (False) preserves every existing caller's bytes exactly."""
    if sampling == 'shift':
        try:
            ss = _shift_start(dt.datetime.fromisoformat(iso))
            return f"{ss.strftime('%-d %b')} {SHIFT_LABELS.get(ss.hour, '')}".strip()
        except (TypeError, ValueError):
            return (iso or '')[:10]
    if sampling in _SAMPLING_SECONDS or sampling in ('minute', 'hour'):
        hhmm = (iso or '')[11:16] or (iso or '')[:10]
        if multiday:
            try:
                return f"{dt.datetime.fromisoformat(iso).strftime('%-d %b')} {hhmm}".strip()
            except (TypeError, ValueError):
                return hhmm
        return hhmm
    try:
        return dt.datetime.fromisoformat(iso).strftime('%-d %b')
    except (TypeError, ValueError):
        return (iso or '')[:10]


def shift_name(iso):
    """IST shift letter (A 06-14, B 14-22, C 22-06) for a bucket-start iso."""
    try:
        d = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return SHIFT_LABELS.get(_shift_start(d).hour)


def in_shift(iso, shift):
    """True if the shift bucket at ``iso`` belongs to the selected shift (validator
    #19). ``shift`` is an FE token: 'all'/'a'/'b'/'c' (or None) keeps everything;
    'a'/'b'/'c' keeps only that shift. One source of truth so every by-shift screen
    filters identically (mirrors panel_powerdemand's inline test)."""
    if not shift or shift == 'all':
        return True
    name = shift_name(iso)
    return bool(name) and name.lower() == shift.lower()


def collapse_to_shift(timeline, sum_keys=(), max_keys=()):
    """Roll hourly timeline buckets up into IST 8h shift buckets. fetch_bucketed
    / fetch_event_counts only do date_trunc (hour/day), so shift is aggregated in
    Python: fetch hourly, then collapse here. sum_keys add, max_keys take the max."""
    out = {}
    for iso, v in timeline.items():
        try:
            d = dt.datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=_IST_TZ)
        d = d.astimezone(_IST_TZ)
        sd = _shift_start(d)
        g = out.setdefault(sd.isoformat(), {})
        for k in sum_keys:
            g[k] = g.get(k, 0) + (v.get(k) or 0)
        for k in max_keys:
            g[k] = max(g.get(k, 0.0), v.get(k) or 0.0)
    return out


def grid_fill(timeline, start, end, sampling, empty):
    """Ordered ``[(bucket_iso, value)]`` over a CONTIGUOUS IST grid — data gaps
    (a bucket with no rows) become ``empty`` so charts have no holes. Matches the
    fetched timeline (keys = bucket isoformats, possibly naive=IST) by UTC instant."""
    grid = bucket_grid(start, end, sampling)
    if not grid:
        return [(k, timeline.get(k, empty)) for k in sorted(timeline)]
    by_inst = {}
    for k, v in timeline.items():
        try:
            d = dt.datetime.fromisoformat(k)
        except (TypeError, ValueError):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=_IST_TZ)
        by_inst[d.astimezone(dt.timezone.utc)] = v
    return [(g.isoformat(), by_inst.get(g.astimezone(dt.timezone.utc), empty)) for g in grid]


def _window_start(cur, table, wc, seconds):
    """Latest timestamp on ``table`` minus ``seconds`` (a datetime), via the
    indexed ``max({wc})``. None if the table is empty. Handles native (max = a
    datetime) and text ISO (max = a string) time columns — the interval is done
    in Python so the raw indexed column can drive the range WHERE."""
    cur.execute(f'SELECT max({wc}) AS m FROM "{table}"')
    row = cur.fetchone()
    mx = (row['m'] if isinstance(row, dict) else row[0]) if row else None
    if mx is None:
        return None
    latest = mx if isinstance(mx, dt.datetime) else _parse_iso(mx)
    return None if latest is None else latest - dt.timedelta(seconds=int(seconds))


def fetch_window(dsn, table, columns, seconds=60, time_col='timestamp'):
    """Rows over the last N seconds, relative to the table's latest timestamp."""
    _safe([table, time_col, *columns])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        want = _augment(columns, dsn, have)
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        sel = [c for c in want if c in have]
        missing = [c for c in want if c not in have]
        # schema-alias: source a missing canonical column from the table's real
        # (differently-named) columns before treating it as absent — same as
        # fetch_live. Without this the snapshot rows drop e.g. apparent_energy_kvah
        # on a PM8000 table (which logs apparent_energy_delivered_kvah), so a
        # counter-derived period energy fell back to a synthesized √(P²+Q²) in the
        # snapshot while ticks (alias-aware) used the real counter — BE-67.
        alias_sel, real_missing = [], []
        for c in missing:
            expr = _alias_expr(have, c)
            (alias_sel.append(f'{expr} AS "{c}"') if expr is not None
             else real_missing.append(c))
        missing = real_missing
        cols = ', '.join([f'{tc} AS "{time_col}"', *(f'"{c}"' for c in sel), *alias_sel])
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            wstart = _window_start(cur, table, wc, seconds)   # latest ts − N, index-friendly
            if wstart is None:
                return []
            cur.execute(f'SELECT {cols} FROM "{table}" WHERE {wc} >= %s ORDER BY {wc} ASC',
                        (bnd(wstart),))
            rows = cur.fetchall()
        shaped = _shape(rows, want, missing, time_col)
        return [_project(_correct_row(r, dsn, _energy_direction(dsn, table)), columns, dsn) for r in shaped]
    finally:
        p.putconn(conn)


# A second timestamp column some tables carry (asset registers have both
# `timestamp` and `ts`); drop the duplicate so the shaped row has one `ts`.
_DUP_TIME_COLS = {'ts'}


def _all_columns_sorted(conn, dsn, table, time_col):
    """Every real column except the time column(s), safe + ordered."""
    have = _columns(conn, dsn, table)
    return sorted(c for c in have if c != time_col and c not in _DUP_TIME_COLS)


def fetch_live_all(dsn, table, time_col='timestamp'):
    """Latest row with EVERY column (introspected). For screen-agnostic asset
    scaffolds where the exact column set isn't pinned down yet."""
    _safe([table, time_col])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        cols = _all_columns_sorted(conn, dsn, table, time_col)
        sel = ', '.join([f'{tc} AS "{time_col}"', *(f'"{c}"' for c in cols)])
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'SELECT {sel} FROM "{table}" ORDER BY {wc} DESC LIMIT 1')
            rows = cur.fetchall()
        shaped = _shape(rows, cols, [], time_col)
        return _correct_row(shaped[0], dsn, _energy_direction(dsn, table)) if shaped else None
    finally:
        p.putconn(conn)


def fetch_window_all(dsn, table, seconds=60, time_col='timestamp'):
    """Rows over the last N seconds with EVERY column. Asset-scaffold sibling of
    :func:`fetch_window`."""
    _safe([table, time_col])
    p = _pool(dsn)
    conn = p.getconn()
    try:
        time_col, tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        cols = _all_columns_sorted(conn, dsn, table, time_col)
        sel = ', '.join([f'{tc} AS "{time_col}"', *(f'"{c}"' for c in cols)])
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            wstart = _window_start(cur, table, wc, seconds)
            if wstart is None:
                return []
            cur.execute(f'SELECT {sel} FROM "{table}" WHERE {wc} >= %s ORDER BY {wc} ASC',
                        (bnd(wstart),))
            rows = cur.fetchall()
        return [_correct_row(r, dsn, _energy_direction(dsn, table)) for r in _shape(rows, cols, [], time_col)]
    finally:
        p.putconn(conn)


def fetch_rows(dsn, table, columns, order_col, limit=50, time_col=None, desc=True):
    """Generic latest-N rows from an arbitrary table (e.g. a DG `_runs` log).

    Unlike the meter helpers this does NOT assume a `timestamp` column; order by
    ``order_col``. If ``time_col`` is given that column is normalized to ``ts``.
    """
    _safe([table, order_col, *columns] + ([time_col] if time_col else []))
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        if not have:                       # table absent (e.g. no `_runs` log on a real DG)
            return []
        sel = [c for c in columns if c in have]
        missing = [c for c in columns if c not in have]
        cols = ', '.join(f'"{c}"' for c in sel) or '*'
        direction = 'DESC' if desc else 'ASC'
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'SELECT {cols} FROM "{table}" '
                        f'ORDER BY "{order_col}" {direction} LIMIT %s', (int(limit),))
            rows = cur.fetchall()
        if time_col:
            return _shape(rows, columns, missing, time_col)
        out = []
        for r in rows:
            d = dict(r)
            for m in missing:
                d[m] = None
            out.append(d)
        return out
    finally:
        p.putconn(conn)


def fetch_rows_since(dsn, table, columns, time_col, since, limit=5000, inclusive=False,
                     flag_true=None):
    """Read-only: rows of ``table`` whose time column is ``> since`` (or ``>=`` if
    ``inclusive``), ordered ASCENDING, capped at ``limit``. The time value is
    normalized to the ``ts`` output key (like ``fetch_live``); missing requested
    columns are padded to None. ``since`` may be an aware datetime, or None to read
    from the earliest row. ``flag_true`` (a boolean column) restricts to rows where
    it is TRUE — so a run-tracker reads only the ``run=true`` samples (a run window
    is a cluster of trues; the gaps between them detect start/stop) instead of the
    whole 5-second stream. Returns [] when the table is absent.

    For the DG run-tracker: paging historical ``dg_N_digital`` rows (``since`` walked
    forward) and the live poller's small incremental read."""
    _safe([table, time_col, *columns] + ([flag_true] if flag_true else []))
    p = _pool(dsn)
    conn = p.getconn()
    try:
        have = _columns(conn, dsn, table)
        if not have:
            return []
        sel = [c for c in columns if c in have]
        missing = [c for c in columns if c not in have]
        rc, _tc, wc, bnd = _tcol(conn, dsn, table, time_col)
        select_cols = [f'"{c}"' for c in sel]
        if rc not in sel:
            select_cols.append(wc)             # wc == f'"{rc}"' → dict key = rc, popped by _shape
        cols = ', '.join(select_cols) or wc
        where, params = [], []
        if since is not None:
            where.append(f'{wc} {">=" if inclusive else ">"} %s')
            params.append(bnd(since))
        if flag_true and flag_true in have:
            where.append(f'"{flag_true}" = true')
        wsql = (' WHERE ' + ' AND '.join(where)) if where else ''
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'SELECT {cols} FROM "{table}"{wsql} ORDER BY {wc} ASC LIMIT %s',
                        (*params, int(limit)))
            rows = cur.fetchall()
        return _shape(rows, columns, missing, rc)
    finally:
        p.putconn(conn)


# ── Multi-table reads ───────────────────────────────────────────────────────
# One logical meter whose columns are split across TWO physical tables on the
# SAME DSN (a DG's `…_main` + `…_engine`, logged in lockstep on one timestamp
# cadence). Each single-table fetcher returns only the columns it has (sampled
# drops absent metrics; live pads them to None), so a per-table call + a
# prefer-non-None merge on the shared time/bucket key reconstructs the full row.
# With one table these are exact pass-throughs (no overhead, no behaviour change).

def _merge_live(dicts):
    """Merge single-row dicts (one per table) into one, preferring non-None so a
    column that's real in one table isn't clobbered by the other's None pad. The
    shared 'ts' (lockstep) carries through. None if every table returned None."""
    merged = None
    for d in dicts:
        if not d:
            continue
        if merged is None:
            merged = dict(d)
            continue
        for k, v in d.items():
            if merged.get(k) is None:
                merged[k] = v
    return merged


def _merge_by_key(lists, key):
    """Merge several bucket/row lists into one, combining rows that share the same
    ``key`` value ('bucket' or 'ts'); prefer non-None. First-seen order preserved."""
    out, order = {}, []
    for rows in lists:
        for r in (rows or []):
            k = r.get(key)
            if k not in out:
                out[k] = dict(r)
                order.append(k)
            else:
                for kk, vv in r.items():
                    if kk != key and out[k].get(kk) is None:
                        out[k][kk] = vv
    return [out[k] for k in order]


# A two-sided asset (HT transformer: HV-in + LV-out, both PM8000) has IDENTICAL
# column names on two tables — a plain union would clobber one side. An optional
# per-table PREFIX renames the colliding table's columns (e.g. LV -> `lv_*`) so
# both sides survive the merge. ``prefixes`` is parallel to ``tables``; '' = none.

def _prefix_dict(d, pf):
    if not pf or not d:
        return d
    return {(k if k in ('ts', 'bucket') else pf + k): v for k, v in d.items()}


def _prefix_rows(rows, pf, key):
    if not pf:
        return rows
    return [{(k if k == key else pf + k): v for k, v in r.items()} for r in (rows or [])]


def _pfx(tables, prefixes):
    return prefixes if prefixes else [''] * len(tables)


def _single(tables, prefixes):
    return len(tables) == 1 and not (prefixes and prefixes[0])


def fetch_live_all_multi(dsn, tables, time_col='timestamp', prefixes=None):
    if _single(tables, prefixes):
        return fetch_live_all(dsn, tables[0], time_col)
    prefixes = _pfx(tables, prefixes)
    return _merge_live([_prefix_dict(fetch_live_all(dsn, t, time_col), pf)
                        for t, pf in zip(tables, prefixes)])


def fetch_live_multi(dsn, tables, columns, time_col='timestamp', prefixes=None):
    if _single(tables, prefixes):
        return fetch_live(dsn, tables[0], columns, time_col)
    prefixes = _pfx(tables, prefixes)
    return _merge_live([_prefix_dict(fetch_live(dsn, t, columns, time_col), pf)
                        for t, pf in zip(tables, prefixes)])


def fetch_window_all_multi(dsn, tables, seconds=60, time_col='timestamp', prefixes=None):
    if _single(tables, prefixes):
        return fetch_window_all(dsn, tables[0], seconds, time_col)
    prefixes = _pfx(tables, prefixes)
    return _merge_by_key([_prefix_rows(fetch_window_all(dsn, t, seconds, time_col), pf, 'ts')
                          for t, pf in zip(tables, prefixes)], 'ts')


def fetch_window_multi(dsn, tables, columns, seconds=60, time_col='timestamp', prefixes=None):
    if _single(tables, prefixes):
        return fetch_window(dsn, tables[0], columns, seconds, time_col)
    prefixes = _pfx(tables, prefixes)
    return _merge_by_key([_prefix_rows(fetch_window(dsn, t, columns, seconds, time_col), pf, 'ts')
                          for t, pf in zip(tables, prefixes)], 'ts')


def fetch_sampled_multi(dsn, tables, start, end, sampling, metrics, time_col='timestamp', prefixes=None):
    if _single(tables, prefixes):
        return fetch_sampled(dsn, tables[0], start, end, sampling, metrics, time_col)
    prefixes = _pfx(tables, prefixes)
    return _merge_by_key([_prefix_rows(fetch_sampled(dsn, t, start, end, sampling, metrics, time_col), pf, 'bucket')
                          for t, pf in zip(tables, prefixes)], 'bucket')


def fetch_bucketed_multi(dsn, tables, start, end, bucket, metrics, time_col='timestamp', prefixes=None):
    if _single(tables, prefixes):
        return fetch_bucketed(dsn, tables[0], start, end, bucket, metrics, time_col)
    prefixes = _pfx(tables, prefixes)
    return _merge_by_key([_prefix_rows(fetch_bucketed(dsn, t, start, end, bucket, metrics, time_col), pf, 'bucket')
                          for t, pf in zip(tables, prefixes)], 'bucket')
