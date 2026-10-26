# Threshold integration for this backend

Implemented 2026-09-29 against `thresholds.md`, adapted to the Group/MFM
architecture. This file takes precedence where the reference document describes
the other backend's model names, modules or payload layout.

## IDs, modules and configuration

- Distribution URLs continue to use **Group.id**; single-MFM URLs use
  **1,000,000 + MFM.id**. No endpoint was renamed or added to a different screen.
- Existing consumers remain in `panels/consumers/distribution.py` and `single.py`.
  We do not create duplicate endpoint classes just to match the reference's
  filenames. New shared rules live in `core/thresholds.py`; voltage/event helpers
  and the connection-local event cache live in `panels/consumers/derived.py`.
- Panel limits resolve field-by-field: non-null **PanelConfig**, then linked
  **EquipmentConfig**, then distribution defaults. Explicit zero is retained.
- RTM bands resolve per metric: **Group RtmThreshold**, then the linked
  equipment's **PanelType RtmThreshold**, then code defaults. A row supplies all
  four bounds. Each row must have exactly one scope, unique per metric/scope.
- MFM Overview and PQ limits use the existing **core.MfmThreshold** overrides.
  Null fields inherit resolved defaults; zero is an override. Overview defaults
  are now available from the config API, not just hidden in card classifiers.
- Positive direct MFM capacity derives contracted kW, critical-load kW and daily
  energy target as documented, unless an explicit MfmConfig value exists.
  Existing explicit MfmConfig rating overrides are retained.

## Admin

| Scope | Admin URL | Effect |
|---|---|---|
| Panel V&C/PQ limits | `/admin/core/distributionpanelthreshold/` | Proxy over **panel_config**, not a duplicate table |
| Panel RTM | `/admin/core/rtmthreshold/` | Select a Group **or** Panel Type |
| Meter Overview/PQ | `/admin/core/mfmthreshold/` | All threshold fields grid-editable |
| Meter inline | `/admin/core/mfm/<raw-id>/change/` | Threshold inline alongside nameplate |
| Panel-type RTM defaults | `/admin/core/paneltype/<id>/change/` | RTM inline |

Save, request `/api/mfm/<navigation-id>/config/` again, and reconnect sockets.
Existing connections retain their resolved configuration. These are display and
monitoring thresholds, not breaker protection settings.

## Page changes and compatibility

| Page / suffix | Rules connected |
|---|---|
| Distribution Overview / Energy & Distribution (`energy-distribution`) | Per-feeder window utilization, gated PF/THD severity; signed reconciliation and 2%/3% review rules |
| Distribution RTM (`real-time-monitoring`) | Saved five-band thresholds; current classification uses kVA load percentage; HT deviation uses matching nominal basis |
| Distribution Energy & Power (`energy-power`) | Configured rated/contracted/critical reference values exposed; no universal Critical badge |
| Distribution V&C (`voltage-current`) | Selected-panel limits, strict `>` boundaries, worst absolute deviation, HT nominal-based events and offline row handling |
| Distribution PQ (`power-quality-summary`) | Selected-panel limits for fleet classifications/events; documented severity and ranking formula |
| MFM Overview (`overview`) | Configured limits with inclusive lower bands, temperature defaults, voltage offline state, frequency magnitude and breaker string handling |
| MFM live Energy & Power (`energy-power`) | Computed SEC trend against connection baseline, using the fixed ±5% rule |
| MFM energy summary (`energy-summary`) | Active-energy percentage of period capacity; SEC trend rule |
| MFM demand analysis (`power-energy-analysis`) | Documented 90%/100% demand bands exposed additively |
| MFM load anomalies (`load-anomalies`) | 85% peak-load trigger, 5-point minimum spread, 150% centre/20-point spread guards, eligibility checks and distinct overload counts |
| MFM V&C (`voltage-current`) | Current-balance limits plus 24-hour numeric event crossings, cached for 60 seconds |
| MFM voltage history (`voltage-history`) | Sag/swell crossings; HT historical deviation recalculated with matching nominal basis |
| MFM PQ (`power-quality-summary`) | Configured THD, H5/H7, flicker and crest limits; Watch at 80%; explicit logger PASS/FAIL verdict retained |

Single-MFM RTM and current/PQ history still do not invent a separate five-band
classifier. Existing reference and history fields remain available.

Compatibility fields:

- Panel RTM retains `cells.<metric>.status`; adds `bands`, kW `band`, and
  top-level `thresholds` containing the effective four boundaries per metric.
- Panel V&C retains `status: normal|warning|danger`; adds the reference vocabulary
  as `highlight: success|warn|danger` and an `online` flag.
- Panel PQ retains `severity: normal|warning|critical`; adds
  `threshold_severity: success|warning|danger`.
- Demand bars retain `band: normal|critical`; add
  `threshold_band: low|moderate|high`. Frontend migration can use that new field
  without breaking existing adapters.
- MFM PQ severity now includes `Watch`, in addition to High/Low. Voltage quality
  includes `flicker_limit`, `flicker_tone`, `crest_factor_ideal`, `crest_tone`.
- Queue snapshots/ticks expose populated `status` maps for available `_status`
  fields. Non-finite numeric JSON values serialize as null.

## Voltage and remaining boundaries of this change

Config exposes `nominal_voltage_ll_v`, `nominal_voltage_ln_v` and
`voltage_avg_basis`. P4's existing real-logger profile uses L-L `voltage_avg`;
legacy sources retain their L-N convention. Threshold comparisons use the same
basis as the measured column. Raw logger readings are not rewritten, and the
stored unbalance fields are not silently recalculated.

The individual-harmonic fallback is now consistently **8%**, replacing this
checkout's prior 6% fallback. Stored overrides are unchanged.

Some prose in the reference covers functionality beyond threshold wiring:
monthly demand-profile population, detailed dominant-driver narrative, and
THD movement trends are not supplied by this change. No table-specific THD
fraction scaling has been guessed: source-unit mappings need independent
verification before normalization. PQ missing inputs can still produce a
passing verdict; clients must inspect value availability as well as severity.
Overview missing numeric measurements can return a null status rather than a
fabricated healthy reading. Source-type classification remains outside scope.

## Deployment and tests

Migration `core.0008_rtm_type_thresholds` adds nullable panel-type RTM scope,
scope/uniqueness constraints, and the admin proxy. Existing per-Group threshold
records and telemetry tables are not rewritten.

Regression tests: `core/tests/test_thresholds.py` covers defaults, overrides,
equality boundaries, PQ verdicts, logger voltage bases, event caching and
consumer snapshots. Existing navigation, configuration and WebSocket routing
tests must also pass before restarting Daphne.

Verification on 2026-09-29: all **255 tests passed** against a temporary
PostgreSQL database; Django system checks and migration consistency checks
passed. Migration 0008 was applied and `cmd-backend-beta.service` restarted.
All **16 endpoint/type combinations** returned live snapshots on port 18889
using HT panel 2 and MFM 1000118 (IDs are verification examples, not frontend
constants). Config returned explicit 415 V L-L / 239.6 V L-N metadata; the
Overview correctly compared a 423.1 V reading to 415 V (1.95% deviation).
