# TF-1 (LV) Frontend Endpoints

Captured with Playwright from the running frontend on `100.90.185.31:3107`.

## Equipment identity

| Field | Value |
|---|---|
| Frontend route | `/equipment/pcc-panels/pcc-1a/incoming/tf-1-lv` |
| Equipment key | `tx-01` |
| MFM ID | `1000026` |
| MFM type | `transformer` |
| Panel ID | `tx-01` |

The frontend uses same-origin HTTP and WebSocket URLs on port `3107`. The
frontend server proxies these requests to the backend.

The common tree endpoints `/api/ems/`, `/api/bms/`, and `/api/assets/` are
intentionally omitted from this document.

## Additional data used with an MFM

The values displayed on these pages do not come only from columns in the MFM's
timeseries table. Before a WebSocket frame is built, the backend combines the
timeseries readings with the following supporting data.

### 1. MFM identity, routing and topology

These values identify the meter, locate it in the electrical hierarchy and tell
the backend what the meter represents.

| Data | Stored on | How it is used |
|---|---|---|
| `id` | `MFM` | Internal meter ID. The frontend-facing ID is `1_000_000 + id`; MFM `26` becomes `1000026`. |
| `name` | `MFM` | Equipment/page label such as `Tf-1 (LV)`. |
| `role` | `MFM` | Classifies the meter as `incoming`, `outgoing`, `spare`, or `coupler`. Used for navigation and input/output topology. |
| `reference` | `MFM -> Equipment` | The panel in which the meter is physically located. TF-1 LV is located in `pcc-1a`. |
| `equipment` | `MFM -> Equipment` | The device measured by the meter. TF-1 LV maps to `tx-01`. This is normally the configuration subject. |
| `asset_category` | `MFM` | Describes the measured category, such as Transformer or LT Panel. |
| `load_profile` | `MFM` | Optional load classification used for equipment grouping/analysis. |
| `parent_series` | `MFM` | Links a meter to its upstream parent meter when input/output relationships are calculated. |
| `Feeder` graph | `Feeder` | Directed `source -> target` topology used to identify upstream input, downstream output and panel relationships. |
| `Breaker` | `Breaker` | Optional breaker type/rating metadata used by SLD and distribution views. It is not live breaker telemetry. |

For this page, the known mapping is:

```text
Frontend MFM ID : 1000026
Database MFM ID : 26
MFM name        : Tf-1 (LV)
Role            : incoming
Located panel   : pcc-1a
Mapped device   : tx-01
Timeseries table: pqm_transformer_1_incomer_pcc_01
MFM type        : transformer
```

### 2. Timeseries connection and correction metadata

These fields decide where readings come from and how raw logger values are
corrected before any endpoint uses them.

| Data | Purpose |
|---|---|
| `data_source` | Selects the timeseries database connection. |
| `table_name` | Selects the physical logger table for this MFM. |
| `series_id` | Mapping/provenance metadata for the MFM series. The current telemetry fetchers do not use it as a SQL row filter. |
| `energy_direction` | Selects the true active-energy register: `import`, `export`, `sum`, or `net`. |
| `energy_scale` | Corrects an energy-register CT/PT multiplier error. It applies to energy only. |
| `power_scale` | Corrects a power-register multiplier error. It applies to kW, kVAr and kVA, not voltage/current. |

These are backend read rules. They are normally not sent to the frontend, but
they change the values that appear in every live and history response.

### 3. Equipment nameplate and operating configuration

The shared configuration is exposed by:

```text
GET /api/mfm/1000026/config/
```

Configuration resolution uses this precedence:

```text
per-MFM override
  -> mapped equipment override (`tx-01`)
  -> located panel override (`pcc-1a`) when needed
  -> equipment/panel-type default
  -> backend fallback
```

The main supporting fields and their uses are:

| Configuration field | Meaning and use |
|---|---|
| `rated_kva` | Nameplate apparent-power capacity. Used for rated capacity, current derivation and utilization/headroom. |
| `rated_kw` | Real-power capacity. Used for load percentage, chart references, energy capacity and load anomalies. |
| `contracted_kw` | Contract/demand reference used by power-analysis bands and limit comparisons. |
| `critical_load_kw` | Critical-load reference for capacity and status calculations. |
| `nominal_voltage_v` / `voltage_nominal_v` | Nominal voltage reference used for voltage deviation, acceptable bands and status. For feeder IDs this is sent on an L-N basis. |
| `voltage_statutory_deviation_pct` | Permitted voltage-deviation band around nominal voltage. |
| `rated_current_a` | Full-load current reference used for loading and current limits. |
| `current_tolerance_pct` | Permitted current tolerance around the rated reference. |
| `current_unbalance_watch_pct` | Current-unbalance watch threshold where configured. |
| `current_high_threshold_a` | High-current limit, normally derived as 120% of rated current. |
| `energy_target_kwh_today` | Daily energy target/capacity used by the Overview and Energy & Power progress displays. |
| `target_efficiency_pct` | Efficiency reference used where an input/output efficiency is available. |
| `frequency_hz` | Nominal frequency reference, normally 50 Hz. |
| `nameplate_voltage_kv` | Optional explicit nameplate voltage displayed in headers. |
| `fault_level_ka` | Optional switchgear fault-level/nameplate value. |

### 4. Per-MFM nameplate values and derived ratings

An MFM may override values that cannot safely be inherited from its mapped
equipment, especially when different meters read different sides of a
transformer.

| Per-MFM field | Behavior |
|---|---|
| `rated_capacity_kva` | Overrides the generic equipment rating for this feeder. |
| `nominal_voltage_v` | Per-meter L-L nominal. It wins over mapped-device and located-panel nominal voltage. |
| `nameplate_voltage_kv` | Per-meter displayed system voltage. |
| `frequency_hz` | Per-meter nominal frequency override. |
| `fault_level_ka` | Per-meter fault-level override. |

The backend derives related values when `rated_capacity_kva` and nominal voltage
are known:

```text
rated_kw                = rated_kva x 0.9
contracted_kw           = rated_kw x 0.9
critical_load_kw        = rated_kw x 0.5
energy_target_kwh_today = rated_kw x 24
rated_current_a         = rated_kva x 1000 / (sqrt(3) x nominal_voltage_L-L)
current_high_threshold  = rated_current_a x 1.2
nominal_voltage_L-N     = nominal_voltage_L-L / sqrt(3)
```

For TF-1 LV, the live resolved configuration is:

| Resolved value | TF-1 LV |
|---|---:|
| `rated_kva` | 2500 kVA |
| `rated_kw` | 2250 kW |
| `contracted_kw` | 2025 kW |
| `critical_load_kw` | 1125 kW |
| L-L nominal used for derivation | 415 V |
| `nominal_voltage_v` / `voltage_nominal_v` sent to feeder pages | 239.6 V L-N |
| `rated_current_a` | 3478 A |
| `current_high_threshold_a` | 4174 A |
| `energy_target_kwh_today` | 54000 kWh |
| `frequency_hz` | 50 Hz |

Important: feeder configuration returns the nominal voltage on an L-N basis.
The frontend may multiply `239.6` by `sqrt(3)` when it needs to display the
corresponding 415 V L-L value. Panel-level configuration follows a different
convention and returns its stored L-L nominal directly.

### 5. Per-MFM thresholds

`MfmThreshold` supplies limits and status boundaries for an individual meter.
Non-null per-MFM values override the mapped equipment/type defaults.

| Threshold | Used for |
|---|---|
| `i_thd_limit_pct` | Current THD limit and IEEE state. TF-1 LV resolves to 8%. |
| `v_thd_limit_pct` | Voltage THD limit and IEEE state. TF-1 LV resolves to 8%. |
| `individual_harmonic_limit_pct` | H5/H7 individual harmonic limit. TF-1 LV resolves to 8%. |
| `flicker_pst_limit` | Flicker Pst reference. TF-1 LV resolves to 1.0. |
| `crest_factor_ideal` | Ideal voltage crest-factor reference. TF-1 LV resolves to 1.414. |
| `overview_busbar_temp_warn_c` / `critical_c` | Overview busbar-temperature status. |
| `overview_load_elevated_pct` / `critical_pct` | Overview load status. TF-1 values are 75% / 90%. |
| `overview_pf_good` / `fair` | Overview PF status. TF-1 values are 0.95 / 0.90. |
| `overview_freq_nominal_hz` | Frequency deviation reference. TF-1 value is 50 Hz. |
| `overview_freq_fair_dev_hz` / `unstable_dev_hz` | Frequency status bands. TF-1 values are 0.05 / 0.10 Hz. |
| `overview_phase_watch_pct` / `unbalanced_pct` | Current phase-balance status. TF-1 values are 5% / 10%. |
| `overview_voltage_dev_elevated_pct` / `critical_pct` | Voltage-deviation status. TF-1 values are 3% / 5%. |
| `overview_energy_elevated_pct` / `critical_pct` | Energy-versus-target status. TF-1 values are 75% / 90%. |

### 6. Viewer and presentation metadata

The 3D object is not an MFM timeseries field. It is resolved from the mapped
equipment key and its viewer rules:

```text
GET /api/equipment/key/tx-01/viewer/?default=panel
```

It supplies:

```text
equipment identity
GLB object slug, label, URL and rating variant
camera position/zoom
lighting and environment
edge, bloom and label settings
animation/flow configuration
```

### 7. Runtime context added by endpoints

Some additional values are computed for a requested window rather than stored
on the MFM:

| Runtime data | How it is obtained |
|---|---|
| `range`, `start`, `end` | Resolved from the query/filter command. |
| `sampling`, `shift` | Determines bucket size and alignment. |
| `available_samplings`, `sampling_by_range` | Backend-advertised legal filter combinations. |
| period energy | Monotonic energy counter minus the counter at the period boundary. |
| load percentage | Active power divided by resolved `rated_kw`. |
| voltage deviation | Measured voltage compared with resolved nominal voltage. |
| current unbalance | Difference between phase currents relative to their mean. |
| status labels | Measured/derived value compared with the applicable threshold. |
| history buckets | Raw rows aggregated with average, minimum or maximum functions. |
| expected load | Historical hour-of-day profile from previous data. |
| anomaly events | Actual load outside the expected statistical band. |
| input/output loss | Topology-aware comparison with the related upstream/downstream meter. |

## Impact of the proposed logger-schema architecture

This section is a design-impact analysis, not a description of the currently
deployed runtime. It assumes the architecture described in
`MFM_LOGGER_SCHEMA_STEP1.md`:

```text
MFM.logger_schema_id -> LoggerSchema
LoggerSchema          -> LoggerSchemaField -> CanonicalField   (Step 2)
```

The purpose of the schema is to normalize the physical fields supplied by a
logger. It does not own the identity, rating, operating target or threshold of
an individual MFM.

### Impact by rollout stage

| Rollout stage | Effect on the endpoints in this document |
|---|---|
| Step 1: add `logger_schema` and `MFM.logger_schema_id` | No telemetry behavior changes. The foreign key records schema identity only; existing consumers and services continue to resolve fields as they do today. |
| Step 2: add and populate the field blueprint | The internal resolution of physical columns changes. Consumers continue to request canonical fields such as `active_power_total_kw`; the assigned schema maps those fields to the correct logger columns and units. |
| Step 3: make schema-based reads authoritative | Global/table-name aliases can be retired after every active MFM has a complete, validated schema assignment. Public response contracts should remain unchanged. |

The architecture is successful only if the frontend does not need logger-specific
field names. A PM8000 MFM and another logger type must expose the same canonical
response key even when their database columns have different names.

### Ownership after the change

| Data or behavior | Owner after the change | Reason |
|---|---|---|
| Physical logger column name | `LoggerSchemaField` | It varies by logger schema. |
| Canonical field name | `CanonicalField` | It is the stable name used by consumers and response payloads. |
| Timestamp column | `LoggerSchema` | It is a table-layout property shared by that schema. |
| Physical-to-canonical unit conversion | `LoggerSchemaField` | For example, a fractional THD value may need a `100` multiplier before it becomes canonical percent. |
| Source expression made from multiple physical columns | `LoggerSchemaField` or its resolver | It describes how that logger supplies one canonical measurement. Only approved/validated expressions should be allowed. |
| `data_source` and `table_name` | `MFM` | They identify where this particular meter is read. |
| `energy_direction`, `energy_scale`, `power_scale` | `MFM` | They correct the installation or meter instance, not the logger family. |
| Nominal voltage, rated kVA/kW/current and energy target | MFM/equipment configuration | These are nameplate and operating values for the individual meter. |
| THD limits and status thresholds | Per-MFM/equipment configuration | These are policy limits, not descriptions of logger columns. |
| Counter delta, average, minimum, maximum and event crossing | Endpoint/service operation | These depend on the requested page and time window. They are not field-name mappings. |
| Utilization, unbalance, deviation, loss, SEC and status formulas | Consumer/domain logic | These are business calculations over canonical values. |

### Read path before and after

Current logical path:

```text
MFM -> data source + table
    -> consumer requests canonical field
    -> global/table-specific alias logic
    -> physical SQL column
    -> correction and endpoint calculation
```

Proposed logical path after the field blueprint is active:

```text
MFM -> data source + table + assigned LoggerSchema
    -> consumer requests canonical field
    -> LoggerSchemaField resolves physical column/expression and source unit
    -> value is normalized to the canonical unit
    -> per-MFM corrections are applied
    -> endpoint aggregation/calculation
    -> stable canonical response key
```

This requires schema context to reach the telemetry service. The current
services receive the data source, table and requested fields, so adding only a
foreign key is not enough to activate schema-based mapping. A conceptual read
context should carry the resolved information together:

```text
MeterReadContext
|-- mfm_id
|-- data_source / DSN
|-- table_name
|-- logger_schema_id
|-- logger_schema_code + version
|-- timestamp_column
|-- resolved canonical field mappings
|-- energy_direction
|-- energy_scale
`-- power_scale
```

Accordingly, service calls conceptually change from:

```text
fetch_live(dsn, table, canonical_fields)
fetch_window(dsn, table, canonical_fields, start, end)
fetch_sampled(dsn, table, canonical_fields, ...)
fetch_bucketed(dsn, table, canonical_fields, ...)
counter_at(dsn, table, canonical_field, boundary)
```

to:

```text
fetch_live(read_context, canonical_fields)
fetch_window(read_context, canonical_fields, start, end)
fetch_sampled(read_context, canonical_fields, ...)
fetch_bucketed(read_context, canonical_fields, ...)
counter_at(read_context, canonical_field, boundary)
```

The names above are explanatory, not a required code signature. The important
constraint is that every read is resolved using the schema assigned to the MFM
being read.

### Shared inherited time-filter contract

Time filtering is independent of the logger schema. The schema answers **which
physical field to read**; the time filter answers **which interval and buckets
to read**. They should meet only at the telemetry service call:

```text
fetch_sampled(
    read_context,             # MFM + logger-schema field resolution
    time_filter.start,
    time_filter.end,
    time_filter.sampling,
    canonical_metrics,
)
```

The current code already has most of the date mathematics in shared service
helpers such as `normalize_range`, `resolve_range`, `custom_window`,
`custom_samplings`, `range_full` and `bucket_grid`. `HistoryConsumer` also
shares query parsing and mid-connection filter changes across the main history
pages. The remaining problem is that this behavior is coupled to a complete
history consumer, while range-only pages such as Energy Summary repeat part of
the same parsing and validation.

The reusable design should therefore have two layers:

1. A pure resolver function that validates raw filter input and returns one
   canonical result.
2. A thin WebSocket mixin that stores that result, handles filter commands and
   adds the common filter envelope to response frames.

#### Canonical filter model

```text
TimeFilterPolicy
|-- default_range
|-- allowed_ranges
|-- sampling_by_range
|-- default_sampling_by_range
|-- custom_sampling_rules
|-- supports_sampling
|-- supports_shift
|-- allowed_shifts = [all, a, b, c]
`-- timezone = Asia/Kolkata

ResolvedTimeFilter
|-- range
|-- start                  # UTC-aware, inclusive
|-- end                    # UTC-aware, exclusive
|-- full_start             # start of the full display grid/capacity window
|-- full_end               # end of the full display grid/capacity window
|-- sampling               # null for a range-only widget
|-- shift                  # all/a/b/c; null when unsupported
|-- available_ranges
|-- available_samplings
|-- sampling_by_range
`-- available_shifts
```

`ResolvedTimeFilter` should be immutable for one build operation. A refresh or
client command creates a new result instead of partially mutating `range`,
`start`, `end`, `sampling` and `shift` on the consumer.

#### Resolver formula

Conceptually, every interactive time filter uses the same function:

```text
F = resolve_time_filter(raw_filter, policy, anchor_now)

F.range             = normalize_and_validate(raw_filter.range, policy)
F.start, F.end       = resolve_window(F.range, raw_filter.start,
                                      raw_filter.end, anchor_now)
F.available_samplings = policy.allowed_sampling(F.range, F.start, F.end)
F.sampling          = validate_or_default(raw_filter.sampling,
                                          F.available_samplings)
F.shift             = validate_or_default_shift(raw_filter.shift, policy)
F.full_start,
F.full_end           = resolve_full_window(F.range, F.start, F.end, anchor_now)
```

The range equations use IST calendar boundaries and UTC-aware output. Let `N`
be the latest logger timestamp converted to IST, `D` be midnight at the start
of `N`'s day, and `M` be the first day of `N`'s month:

| Range | Canonical interval |
|---|---|
| `today` | `[D, N)` |
| `yesterday` | `[D - 1 day, D)` |
| `this_week` | `[D - weekday(N) days, N)` |
| `last_7d` / `last_week` | `[D - 7 days, N)` under the current contract |
| `this_month` | `[M, N)` |
| `last_month` | `[first day of previous month, M)` |
| `last_30d` | `[D - 30 days, N)` |
| `last_24h` | `[N - 24 hours, N)` |
| `custom` with datetimes | `[min(start, end), max(start, end))` |
| `custom` with dates | `[earlier date 00:00 IST, day after later date 00:00 IST)` so the selected end date is included |

`anchor_now` should normally be the latest timestamp in the selected MFM's
logger table, not the application server clock. This preserves correct filters
when the source database contains delayed or historical data.

Shift buckets use one shared definition:

| Shift | IST interval |
|---|---|
| `A` | 06:00 to 14:00 |
| `B` | 14:00 to 22:00 |
| `C` | 22:00 to 06:00 the following day |
| `all` | Do not remove any shift bucket |

The shift is a selection over already aligned eight-hour buckets. It must not
silently change the underlying date range.

#### Inheritable WebSocket behavior

The resolver remains a plain service function so it can also be used by HTTP,
tasks and fixed-window calculations. WebSocket consumers inherit only the
transport behavior:

```python
class TimeFilterMixin:
    TIME_FILTER_POLICY = TimeFilterPolicy(...)

    def read_time_filter(self, query_params, anchor_now): ...
    def apply_time_filter_command(self, command, anchor_now): ...
    def time_filter_envelope(self): ...


class HistoryConsumer(TimeFilterMixin, ...):
    # Fetches and pads bucketed history using self.time_filter.
    ...


class FeederEnergySummaryConsumer(TimeFilterMixin, ...):
    TIME_FILTER_POLICY = TimeFilterPolicy(
        supports_sampling=False,
        supports_shift=False,
        ...,
    )
```

The mixin should own only these common steps:

1. Parse `range`, `start`, `end`, `sampling` and `shift` from the connection
   query string or a WebSocket command.
2. Call the pure resolver with the endpoint's policy.
3. Preserve the existing filter if an invalid command is rejected.
4. Send one consistent typed error for an unsupported combination.
5. Expose the resolved filter envelope in `snapshot` and `update` frames.

It should not fetch telemetry, calculate KPIs or decide which fields an endpoint
needs. Those remain responsibilities of the concrete consumer.

#### Policy inheritance by endpoint

| Endpoint group | Shared behavior | Endpoint policy/operation |
|---|---|---|
| `power-energy-analysis`, `load-anomalies`, `voltage-history`, `current-history`, `power-quality-history` | Inherit the full range, sampling, shift and custom-window behavior. | Declare allowed sampling for each range and perform their own metrics/KPIs. |
| `energy-summary` | Inherit range normalization, custom bounds, command handling and response envelope. | Disable sampling and shift; retain its smaller allowed-range list and energy-capacity calculation. |
| `overview`, `energy-power`, `power-quality-summary` | Call the same pure resolver for fixed internal ranges such as `today` or `this_month`. | Do not expose an interactive filter unless the page contract requires it. |
| `real-time-monitoring`, `voltage-current` | Continue using rolling live windows. | They do not use calendar range/sampling controls and should not inherit unused selector state. |

This distinction is important: sharing the resolver does not mean every page
must show Days, Shifts and Custom Range controls. It means every page that uses
those concepts applies the same normalization, timezone, interval boundaries,
validation and response vocabulary.

#### Common response envelope

Filtered WebSocket frames should derive their metadata from the one resolved
object:

```jsonc
{
  "range": "custom",
  "start": "2026-08-31T18:30:00+00:00",
  "end": "2026-09-20T18:30:00+00:00",
  "sampling": "day",
  "shift": "all",
  "available_ranges": ["today", "yesterday", "last_7d", "custom"],
  "available_samplings": ["day"],
  "sampling_by_range": {"today": ["hour", "shift"]},
  "available_shifts": ["all", "a", "b", "c"]
}
```

Unsupported properties may be omitted or set to null according to the existing
endpoint contract, but their naming should not vary between consumers. Existing
camel-case response aliases can be retained temporarily for frontend
compatibility and removed through a separate versioned contract change.

#### Validation rules

- Normalize spelling variants such as `this-week`, `thisweek` and `this_week`
  before validation, then echo only the canonical token.
- A custom range is valid only when both bounds parse successfully.
- Reversed custom bounds are reordered consistently by the shared resolver.
- A sampling is valid only when allowed by the selected range and custom span.
- `shift=a/b/c` is valid only when shift sampling and shift selection are
  enabled; otherwise use `all` or reject it consistently.
- Unknown connection-query values may fall back to the policy default for
  backward compatibility. Invalid mid-connection commands should return an
  error without changing the current resolved filter.
- Bucket alignment, display-grid padding and labels must use the same timezone
  and shift anchor as SQL aggregation.
- Tests should exercise the pure resolver as a range/sampling matrix. Consumer
  tests then need only confirm command handling and endpoint-specific output.

### Endpoint-by-endpoint impact

| Endpoint | Logger fields resolved through the assigned schema | What remains unchanged |
|---|---|---|
| `overview` | Energy counters; total power; PF; voltage; frequency; phase/neutral currents; demand; busbar and live breaker measurements. | Counter deltas, utilization, deviation, demand, status formulas, ratings, targets and thresholds. The response keys and frame structure remain canonical. |
| `real-time-monitoring` | Every field listed in the live `columns` set, including power, energy, voltage and current fields. | Queue/tick behavior, timestamps, rolling-window handling, fallback phase averages and power-rate calculation. |
| `energy-power` | Energy counters, instantaneous power and fields read from paired/upstream/downstream meters. | Period deltas, budget, load factor, loss and percentage formulas; targets and ratings remain per MFM. Each related MFM must use its own schema. |
| `energy-summary` | Active/reactive counters for the selected MFM and every downstream MFM used by SEC. | Counter-delta windows, apparent-energy calculation, capacity target and SEC formula. Each downstream meter must use its own schema. |
| `power-energy-analysis` | Active, reactive and apparent power. | Bucket average/max operations, demand bands and rated/contract reference lines. |
| `load-anomalies` | Active-power samples and any directly logged load KPI. | Hour-of-day baseline, statistical range, surge/dip/overload detection and conversion to percent of rated kW. |
| `voltage-current` | Phase/average voltage, phase/neutral current and logger-supplied unbalance fields. | Fallback mean/unbalance calculations, CT deadband, nominal/rated references and health classification. |
| `voltage-history` | Phase voltages and any logger-supplied deviation fields. | Bucket aggregation, worst-spread logic, sag/swell crossing counts and the nominal-voltage expected band. |
| `current-history` | Phase/neutral currents and logger-supplied unbalance fields. | Bucket average/max operations, fallback unbalance calculation and rated/tolerance bands. |
| `power-quality-summary` | V-THD, I-THD, H5/H7, flicker, crest factor, K-factor, PF and phase-angle measurements. | IEEE/limit comparison, trend/severity, likely-source logic and all configured limits. Logger-specific unit normalization moves into field mapping. |
| `power-quality-history` | Historical THD, harmonic, PF, phase-angle and K-factor fields. | Time buckets, average/min/max KPIs, PF gap and configured reference lines. Logger-specific unit normalization occurs before endpoint calculations. |
| `GET /api/mfm/1000026/config/` | None of its existing values need logger-field resolution. | All ratings, nominal voltage, target and threshold fields keep their current meaning. An optional schema identity block could be added later for administration, but the page does not require it. |
| `GET /api/equipment/key/tx-01/viewer/` | None. | Object, camera, lighting and presentation data are unrelated to the logger schema. |

The route paths, MFM ID, query/filter commands, WebSocket lifecycle, page names
and existing canonical response keys do not need to change for any row above.

### Critical migration rules

1. **Resolve related MFMs independently.** `energy-power` and `energy-summary`
   read other meters through topology. A related meter may use a different
   logger schema, so its read context cannot inherit TF-1 LV's schema.
2. **Normalize units exactly once.** The present PM8000 handling multiplies
   fractional THD by `100` based on table-specific logic. Once the schema field
   mapping performs that conversion, the old consumer conversion must be
   removed at the same migration boundary to prevent values being multiplied
   twice. The same rule applies to live and historical THD.
3. **Aggregate canonical values.** Unit conversion and source-field resolution
   should happen before averages, maxima, counter deltas or threshold crossing
   calculations. This keeps operations consistent across logger types.
4. **Validate assignments before strict reads.** An active schema assignment
   should be rejected when required canonical fields, the timestamp column or
   source types are invalid for the logger table.
5. **Keep optional fields nullable.** A schema may legitimately lack an
   optional measurement such as H7 or K-factor. The endpoint should retain the
   canonical key with a null value where its existing contract requires it.
6. **Do not silently mix resolvers.** During rollout, an unassigned MFM may use
   an explicit legacy compatibility path. Once an MFM is schema-managed, a
   missing required mapping should produce a clear configuration error rather
   than falling through to an unrelated global alias.
7. **Protect historical assignments.** Retiring a schema prevents new
   assignments but must not break MFMs already using it. Deletion remains
   protected while an MFM references the schema.

### Expected frontend result

For TF-1 LV, a successful migration should produce the same URLs, commands,
frame types, canonical keys, units and UI calculations documented below. Only
the backend's path from a canonical field to its physical logger column changes.
That makes a second MFM with different physical column names usable by the same
consumers without adding more table-name conditionals to the service layer.

## Per-endpoint: what is shown and how it is produced

The tables below use these source labels:

- **Direct**: latest corrected value from the MFM timeseries table.
- **Window**: aggregation or counter delta over a selected time window.
- **Derived**: calculated from one or more readings.
- **Config**: resolved nameplate, target or threshold data described above.
- **Topology**: calculated using a related upstream/downstream MFM.

### `/ws/mfm/1000026/overview/`

| UI content | Inputs | How it is shown |
|---|---|---|
| Energy Consumption Today | Active, reactive and apparent import counters; daily target | **Window:** current counter minus midnight counter. Percentage = active kWh / `energy_target_kwh_today` x 100. Status uses the Overview energy thresholds. |
| Rated Capacity Utilization | `active_power_total_kw`, `reactive_power_total_kvar`, `apparent_power_total_kva`; `rated_kw`, `rated_kva` | Power values are **Direct**. Load % is **Derived** as `abs(active kW) / rated_kw x 100`; status uses 75%/90% boundaries. |
| Power Factor | Total and per-phase PF, displacement PF, true PF | Values are **Direct**. Harmonic gap is **Derived** as displacement PF minus true PF. Status compares PF with 0.95/0.90. |
| Voltage Deviation | `voltage_avg`; L-N nominal | Deviation is **Derived** as `(actual - nominal) / nominal x 100`. Status compares absolute deviation with 3%/5%. |
| Grid Frequency | `frequency_hz`; nominal frequency | Actual frequency is **Direct**. Deviation is `actual - nominal`; status uses 0.05/0.10 Hz bands. |
| Current Unbalance | R/Y/B/neutral/average current and logger unbalance | Currents and unbalance are **Direct** where present. A CT deadband of `max(1% rated current, 1 A)` decides Idle versus active classification; active status uses 5%/10%. |
| Demand Profile | Active/apparent demand history; `rated_kva` | Today's peak is a **Window max**. The radar is daily maximum kVA for the current month. Excursion days are days whose peak exceeds rated kVA. |
| R/Y/B/Total strip | Per-phase voltage, current, power and PF | Latest corrected **Direct** readings grouped by phase. |
| Busbar/Breaker cards | Busbar temperature and breaker fields | Shown directly when logged; otherwise values remain null. Status comes from configured thresholds. |

### `/ws/mfm/1000026/power-energy-analysis/`

| UI content | Inputs | How it is shown |
|---|---|---|
| Active Power bars | `active_power_total_kw` | **Window:** average active power for each selected bucket. |
| Reactive Power bars | `reactive_power_total_kvar` | **Window:** average reactive power for each bucket. |
| Apparent Power | Bucketed active/reactive power | **Derived** as the active/reactive vector magnitude when required. |
| Demand bars | `active_power_total_kw` | **Window:** maximum active power in each bucket. |
| Demand band | Bucket demand; `contracted_kw` | **Derived** by comparing bucket maximum demand with the contracted reference. |
| Rated/contract lines | `rated_kw`, `contracted_kw` | **Config** reference lines; they are not MFM readings. |
| Overview demand chart | Same endpoint with `range=last_7d` | The Overview requests daily/period buckets from this endpoint and renders peak/average demand. |

### `/ws/mfm/1000026/real-time-monitoring/`

| UI content | Inputs | How it is shown |
|---|---|---|
| Power & Energy chart | Active/reactive/apparent power and import energy | Latest values are **Direct**. The frontend maintains the visible live line from the rolling `queue[]` plus subsequent `tick.enqueue[]` rows. |
| Voltage Monitor | R/Y/B L-N voltage or RY/YB/BR L-L voltage | Values are **Direct**. `voltage_ll_avg` is **Derived** as the mean of available L-L pairs when the logger leaves it null. |
| Current Monitor | R/Y/B/neutral current | Values are **Direct**. Average/min/max are read directly where present and otherwise supported by derived phase statistics in the feeder consumers. |
| Power rate | Consecutive active-power samples | **Derived** as change in active kW divided by elapsed minutes. |

### `/ws/mfm/1000026/energy-power/`

| UI content | Inputs | How it is shown |
|---|---|---|
| Today/week/month energy | Monotonic active/reactive/apparent counters | **Window:** counter at latest sample minus counter at the selected period boundary. |
| Budget difference | Period active energy; daily energy target; elapsed day fraction | **Derived:** active energy minus the time-prorated target. |
| kW load percentage | Active power; `rated_kw` | **Derived:** active kW / rated kW x 100. |
| Instantaneous power | Active, reactive and apparent total power | Latest corrected **Direct** values. |
| Peak demand and load factor | Active-power samples in the current window | **Window:** peak is maximum kW; load factor is average demand relative to peak demand. |
| Input vs Output | Current MFM energy plus related panel/incomer MFM | **Topology:** outgoing feeders show their share of panel input; transformer/incoming meters can show input, output and loss when a valid paired meter exists. |
| Active loss | HV input and LV output power | **Derived:** `input - output`; loss percentage = loss / input x 100. Invalid/unavailable pairings remain null. |
| Trend status fields | Window metrics | Backend status words such as `stable` are sent in the frame `status` map and rendered as pills. |

### `/ws/mfm/1000026/energy-summary/`

| UI content | Inputs | How it is shown |
|---|---|---|
| Headline apparent energy | Period active/reactive counters | **Window + Derived:** active and reactive are counter deltas; apparent period energy is their vector magnitude. |
| Active and reactive metrics | Import counters | **Window:** selected-period counter deltas. |
| Energy capacity/target | `rated_kw`; full selected-window hours | **Config + Derived:** `rated_kw x window hours`. This is a theoretical capacity ceiling. |
| Progress segments | Period energies and target | Values are expressed against the calculated capacity and returned as ready-to-render segments. |
| SEC | Active energy and downstream-meter active energy | **Topology + Derived:** upstream active energy / sum of downstream active energy. Null when downstream energy is unavailable. |

### `/ws/mfm/1000026/load-anomalies/`

| UI content | Inputs | How it is shown |
|---|---|---|
| Actual Load | Bucket average active power; `rated_kw` | **Window + Derived:** average kW / rated kW x 100. |
| Expected Load | Previous 30 days of active power | **Window:** hour-of-day historical profile, converted to load %. The profile level is aligned to the selected window while preserving its hourly shape. |
| Expected Range | Historical profile spread | **Derived:** expected load plus/minus its bounded statistical spread. |
| Surge/Dip markers | Actual bucket average/min/max versus expected range | **Derived:** outside the expected band becomes a surge or dip. |
| Overload | Bucket maximum load; maximum threshold | **Derived:** a separate absolute check for maximum load above the configured/default limit. |
| Present value | Latest load percentage | Direct logger KPI when present; otherwise active kW / rated kW x 100. |

### `/ws/mfm/1000026/voltage-current/`

| UI content | Inputs | How it is shown |
|---|---|---|
| Voltage Live Health | R/Y/B voltages, average voltage, nominal voltage | Phase values are **Direct**. Deviation is recalculated against the resolved nominal where required. |
| Voltage unbalance/max gap | Phase voltage readings and logger KPIs | Direct logger KPI where available; phase gaps are shown from the latest row. |
| Current Live Health | R/Y/B/neutral currents | Phase values are **Direct**. Missing average is **Derived** as the mean of available phases. |
| Current unbalance | R/Y/B currents | Logger value is used when present; otherwise **Derived** as `(max phase - min phase) / mean phase x 100`, above the CT deadband. |
| Phase deviations | Each phase current and phase mean | **Derived:** `(phase - mean) / mean x 100` when real current is above the deadband. |
| Health/status label | Unbalance, average current, rated current, thresholds | **Derived** using the same classifier as Overview, ensuring both pages show the same status word. |

### `/ws/mfm/1000026/voltage-history/`

| UI content | Inputs | How it is shown |
|---|---|---|
| R/Y/B history lines | Phase voltage readings | **Window:** average phase voltage per selected bucket. |
| Expected band | Resolved nominal voltage and statutory/configured deviation | **Config:** upper/lower band around nominal; not measured timeseries data. |
| Maximum deviation | Bucket/sample voltage deviation | **Window:** worst absolute deviation in the selected range. |
| Worst spread | Phase minimum/maximum values | **Derived/Window:** largest phase-to-phase spread and its phase pair. |
| Sag/Swell event totals | Voltage crossing configured event boundaries | **Window:** threshold-crossing counts for the selected range. |

### `/ws/mfm/1000026/current-history/`

| UI content | Inputs | How it is shown |
|---|---|---|
| R/Y/B history lines | Phase currents | **Window:** average phase current per selected bucket. |
| Expected/rated band | `rated_current_a`, current tolerance/high limit | **Config:** reference band and limit lines; not logger history. |
| Peak Current | Per-sample/bucket current maxima | **Window:** maximum current in the selected range. |
| Average Current | Bucket average currents | **Window:** selected-range average. |
| Maximum Unbalance | Current-unbalance samples | **Window:** maximum unbalance percentage. |
| Neutral Peak | Neutral-current samples | **Window:** maximum neutral current. |

### `/ws/mfm/1000026/power-quality-summary/`

| UI content | Inputs | How it is shown |
|---|---|---|
| IEEE 519 state | Worst phase V-THD/I-THD, H5/H7; configured limits | **Derived:** fail when a measured value exceeds its limit; constraint identifies current or voltage. |
| I-THD and V-THD bars | Per-phase THD values | **Direct after unit normalization.** PM8000 fractional THD is multiplied by 100; meters already storing percent are unchanged. Headline uses the worst phase, not the mean. |
| H5/H7 | Individual harmonic columns | **Direct**, compared with the individual-harmonic limit. Missing measurements remain null. |
| Trend and severity | THD movement and limit utilization | **Derived:** rising/falling/flat from movement per hour; severity is High on failure, Watch near 80% of a limit, otherwise Low. |
| Flicker | Live Pst plus today's maximum | Live value is **Direct**; peak is a **Window max**; tone compares it with `flicker_pst_limit`. |
| Crest factor | Measured crest factor; ideal reference | **Direct + Config:** tone is based on distance from 1.414. |
| K-factor | Measured transformer harmonic-loading factor | **Direct** when logged; otherwise null. |
| Source & mitigation | Dominant harmonic and configuration flags | **Derived/Config:** maps harmonic order to a likely source and reports configured filters/APFC state. |

### `/ws/mfm/1000026/power-quality-history/`

| UI content | Inputs | How it is shown |
|---|---|---|
| V-THD profile | R/Y/B voltage THD | **Window:** average per phase per bucket; selected-range KPIs provide average and maximum. |
| I-THD profile | R/Y/B current THD | **Window:** average per phase per bucket; selected-range KPIs provide average and maximum. |
| H5/H7 history | 5th/7th harmonic readings | **Window:** bucket average when logger columns exist; otherwise values/KPIs are null. |
| PF Health | Total PF and true PF | **Window:** bucket average/minimum; configured PF target/reference is rendered separately. |
| PF gap | Displacement PF and true PF | **Derived:** displacement PF minus true PF. |
| PF Angle | Phase angle samples | **Window:** average phase angle per bucket. |
| K-Stress | K-factor samples | **Window:** average/max when available; otherwise null. |

### `/api/mfm/1000026/config/`

This endpoint does not display a standalone widget. It supplies the **Config**
references used throughout the other endpoints: ratings, nominal voltage,
current limits, energy target, PF/THD limits and Overview status thresholds.

### `/api/equipment/key/tx-01/viewer/`

This endpoint drives the Overview 3D model. It does not contain electrical
telemetry. The frontend uses its object URL, camera, lighting and rendering
configuration to present the transformer/panel model.

## Shared WebSocket behavior

Live column-row sockets initially return a full snapshot:

```jsonc
{
  "type": "snapshot",
  "mfm_id": 1000026,
  "mfm_name": "Tf-1 (LV)",
  "panel_id": "tx-01",
  "mfm_type": "transformer",
  "page": "real-time-monitoring",
  "window_seconds": 900,
  "capacity": 12,
  "columns": ["..."],
  "count": 12,
  "queue": [{ "ts": "ISO-8601", "...": "metric values" }],
  "status": {}
}
```

New live readings arrive as tick frames:

```jsonc
{
  "type": "tick",
  "enqueue": [{ "ts": "ISO-8601", "...": "metric values" }],
  "dequeue": 1,
  "queue_size": 12,
  "status": {}
}
```

History/chart sockets accept filters either in the connection query string or
as a JSON message over the open socket:

```json
{"range":"today","sampling":"hour","shift":"a","start":"2026-09-01","end":"2026-09-20"}
```

Only fields applicable to the selected range are required. For example:

```json
{"range":"last_7d","sampling":"day"}
```

## 1. Overview

### Overview widgets

```text
ws://100.90.185.31:3107/ws/mfm/1000026/overview/
```

Response structure:

```jsonc
{
  "type": "snapshot",
  "mfm_id": 1000026,
  "mfm_name": "Tf-1 (LV)",
  "panel_id": "tx-01",
  "mfm_type": "transformer",
  "page": "overview",
  "ts": "ISO-8601",
  "widgets": {
    "busbar_temperature": { "value_c": null, "rise_c": null, "status": null },
    "energy_consumption": {
      "today_kwh": 0,
      "target_kwh": 0,
      "pct_of_target": 0,
      "today_reactive_kvarh": 0,
      "today_apparent_kvah": 0,
      "budget_delta_kwh": null,
      "status": "On Track"
    },
    "kw_load": {
      "pct": 0,
      "active_kw": 0,
      "rated_kw": 2250,
      "rated_kva": 2500,
      "reactive_kvar": 0,
      "apparent_kva": 0,
      "load_factor": 0,
      "status": "Normal"
    },
    "power_factor": {
      "value": 0,
      "displacement_pf": 0,
      "true_pf": 0,
      "harmonic_gap": 0,
      "phases": {},
      "status": "Good"
    },
    "voltage_deviation": {
      "pct": 0,
      "actual_v": 0,
      "nominal_v": 239.6,
      "limit_pct": 10,
      "phases": {},
      "phase_v": {},
      "status": "Normal"
    },
    "grid_frequency": { "hz": 0, "deviation_hz": 0, "nominal_hz": 50, "status": "Stable" },
    "phase_balance": {
      "pct": 0,
      "neutral_a": 0,
      "avg_phase_a": 0,
      "r": 0,
      "y": 0,
      "b": 0,
      "deviation": {},
      "status": "Balanced",
      "deadband_a": 0
    },
    "breaker": { "state": null, "trips_24h": null, "status": "Unknown" },
    "demand_profile": {
      "peak_today_kw": 0,
      "max_kw": 0,
      "daily_peaks_kva": [],
      "peak_this_month": null,
      "monthly_avg_kva": 0,
      "contract_limit_kva": 0,
      "excursion_days": []
    },
    "phase_readings": { "R": {}, "Y": {}, "B": {}, "total": {}, "line_voltages": {} },
    "ai_summary": null
  }
}
```

### Demand profile

```text
ws://100.90.185.31:3107/ws/mfm/1000026/power-energy-analysis/?range=last_7d
```

This uses the Power Energy Analysis payload documented under Energy & Power.

### 3D viewer configuration

```text
GET http://100.90.185.31:3107/api/equipment/key/tx-01/viewer/?default=panel
```

Response structure:

```text
equipment {id,key,kind,type,pageType}
object    {slug,label,url,rating}
viewer    {toneMap,lighting,animProps,flowMixes,environment,bloom,edges,pills,camera,...}
```

## 2. Real Time Monitoring

```text
ws://100.90.185.31:3107/ws/mfm/1000026/real-time-monitoring/
```

This is a live column-row socket using the shared snapshot/tick envelopes.
Each `queue[]` or `enqueue[]` row can contain:

```text
ts
active_power_total_kw
reactive_power_total_kvar
apparent_power_total_kva
active_energy_import_kwh
reactive_energy_import_kvarh
apparent_energy_kvah
rate_of_change_power_kw_per_min
voltage_r_n, voltage_y_n, voltage_b_n
voltage_avg, voltage_max, voltage_min
voltage_ry, voltage_yb, voltage_br, voltage_ll_avg
current_r, current_y, current_b, current_neutral
current_avg, current_max, current_min
```

No client payload is required to start this socket.

## 3. Energy & Power

### Live energy and power

```text
ws://100.90.185.31:3107/ws/mfm/1000026/energy-power/
```

This uses the shared snapshot/tick envelopes. Row fields include:

```text
active_energy_import_kwh, reactive_energy_import_kvarh, apparent_energy_kvah
active_energy_today_kwh, reactive_energy_today_kvarh, apparent_energy_today_kvah
active_energy_this_week_kwh, reactive_energy_this_week_kvarh, apparent_energy_this_week_kvah
active_energy_this_month_kwh, reactive_energy_this_month_kvarh, apparent_energy_this_month_kvah
cumulative_vs_budget_kwh, kpi_kw_load_pct_of_rated
hv_input_kw, lv_output_kw, active_power_loss_kw, active_power_loss_pct
loss_energy_today_kwh
active_power_total_kw, reactive_power_total_kvar, apparent_power_total_kva
specific_energy_consumption, kpi_load_factor
peak_demand_today_kw, peak_demand_at_time, power_rate_kw_per_h
sec_trend_status, load_factor_trend_status
peak_demand_trend_status, power_trend_status
io_mode, io_input_mfm_id, io_input_mfm_name
io_feeder_kwh, io_panel_incoming_kwh, io_share_pct
io_panel, io_incomers, output_energy_today_kwh
```

### Today's energy summary

```text
ws://100.90.185.31:3107/ws/mfm/1000026/energy-summary/?range=today
```

Response structure:

```text
{type,mfm_id,mfm_name,panel_id,mfm_type,page,ts,range,start,end,available_ranges,
 headline:{label,value,unit,target,pct_of_target,meta,tone},
 progress:{segments:[...],capacity,marker_pct},
 metrics:[{key,label,value,unit,tone,trend}],
 insight}
```

### Power analysis

```text
ws://100.90.185.31:3107/ws/mfm/1000026/power-energy-analysis/?range=today&sampling=hour
```

Response structure:

```text
{type,mfm_id,mfm_name,panel_id,mfm_type,page,ts,shift,available_shifts,
 data:{
   period,periodOptions,sampling,availableSamplings,view,
   bars:[{time,active,reactive,apparent,shift,shiftTiming}],
   demandBars:[{time,value,band,shift,shiftTiming}],
   hourlyAverage:[],ratedKw,contractedKw,
   activePowerAvgKw,reactivePowerAvgKw,apparentPowerAvgKva,
   yMin,yMax,demandYMin,demandYMax,insight,
   demandBandLegend:[{band,label,threshold,color}]
 }}
```

### Load anomalies

```text
ws://100.90.185.31:3107/ws/mfm/1000026/load-anomalies/?range=today&sampling=hour
```

Response structure:

```text
{type,mfm_id,mfm_name,panel_id,mfm_type,page,ts,shift,available_shifts,
 data:{
   period,periodOptions,sampling,availableSamplings,
   actualLoad:[{time,value,shift,shiftTiming}],
   expectedLoad:[{time,value}],
   expectedRange:[{time,min,max}],
   anomalies:[],maxThresholdPct,presentValuePct,
   surgeEvents,dipEvents,overloadEvents,loadFactorPct,
   yMin,yMax,insight,labels,colors
 }}
```

## 4. Voltage & Current

### Live voltage and current

```text
ws://100.90.185.31:3107/ws/mfm/1000026/voltage-current/
```

This uses the shared snapshot/tick envelopes. Row fields include:

```text
voltage_r_n, voltage_y_n, voltage_b_n
voltage_avg, voltage_max, voltage_min
voltage_r_deviation_pct, voltage_y_deviation_pct, voltage_b_deviation_pct
kpi_voltage_deviation_pct, voltage_unbalance_pct
voltage_ry, voltage_yb, voltage_br, voltage_ll_avg
current_r, current_y, current_b, current_neutral
current_avg, current_max, current_min
current_r_deviation_pct, current_y_deviation_pct, current_b_deviation_pct
current_unbalance_pct, current_max_spread
current_spread_br, current_spread_ry, current_spread_by
kpi_neutral_to_phase_ratio_pct
sag_events_24h, swell_events_24h
current_balance_status, current_deadband_a
```

### Voltage history

```text
ws://100.90.185.31:3107/ws/mfm/1000026/voltage-history/?range=today&sampling=hourly
```

Response structure:

```text
{type,mfm_id,mfm_type,page,range,start,end,sampling,
 available_samplings,sampling_by_range,shift,available_shifts,columns,count,
 buckets:[{
   voltage_r_n_avg,voltage_y_n_avg,voltage_b_n_avg,
   voltage_ry_avg,voltage_yb_avg,voltage_br_avg,
   vdev_max,vdev_min,vamx,vamn,vmax,vmin,bucket
 }],
 kpis:{max_deviation_pct,worst_spread_v,worst_spread_pair,sag_events,swell_events}}
```

### Current history

```text
ws://100.90.185.31:3107/ws/mfm/1000026/current-history/?range=today&sampling=hourly
```

Response structure:

```text
{type,mfm_id,mfm_type,page,range,start,end,sampling,
 available_samplings,sampling_by_range,shift,available_shifts,columns,count,
 buckets:[{current_r_avg,current_y_avg,current_b_avg,cmax,cavg,cunb_max,cneut_max,bucket}],
 kpis:{peak_current_a,average_current_a,max_unbalance_pct,neutral_peak_a}}
```

## 5. Power Quality

### Power-quality summary

```text
ws://100.90.185.31:3107/ws/mfm/1000026/power-quality-summary/
```

Response structure:

```text
{type,mfm_id,mfm_name,panel_id,mfm_type,page,ts,
 widgets:{
   feeder_pq_state:{ieee_state,ieee_constraint,trend,severity,limits_source},
   feeder_thd:{
     i_thd:{value_pct,limit_pct,scale_max_pct,phases:{r,y,b}},
     v_thd:{value_pct,limit_pct,scale_max_pct,phases:{r,y,b}}
   },
   feeder_individual_harmonics:{
     h5:{value_pct,limit_pct,scale_max_pct},
     h7:{value_pct,limit_pct,scale_max_pct}
   },
   feeder_voltage_quality:{flicker_pst,crest_factor,k_factor},
   feeder_source_mitigation:{
     likely_source,filter_state,capacitor_bank,next_priority,next_priority_tone
   }
 }}
```

### Power-quality history

```text
ws://100.90.185.31:3107/ws/mfm/1000026/power-quality-history/?range=today&sampling=2hour
```

Response structure:

```text
{type,mfm_id,mfm_type,page,range,start,end,sampling,
 available_samplings,sampling_by_range,shift,available_shifts,columns,count,
 buckets:[{
   thd_voltage_r_pct_avg,thd_voltage_y_pct_avg,thd_voltage_b_pct_avg,
   thd_current_r_pct_avg,thd_current_y_pct_avg,thd_current_b_pct_avg,
   voltage_avg_avg,
   thd_compliance_v_avg_avg,thd_compliance_v_avg_max,
   thd_compliance_i_avg_avg,thd_compliance_i_avg_max,
   power_factor_total_avg,power_factor_total_min,
   kpi_true_pf_avg,kpi_true_pf_min,kpi_displacement_pf_avg,
   phase_angle_deg_avg,bucket
 }],
 kpis:{
   v_thd_avg,i_thd_avg,v_thd_max,i_thd_max,
   pf_avg,pf_min,true_pf_avg,true_pf_min,
   k_factor_max,pf_displacement_gap_avg,h5_avg,h7_avg
 }}
```

## Equipment configuration

Used across the pages for ratings, targets, reference lines and status limits:

```text
GET http://100.90.185.31:3107/api/mfm/1000026/config/
```

Response structure:

```text
{mfm_id,panel_id,mfm_type,config_table,
 config:{
   rated_kva,rated_kw,contracted_kw,critical_load_kw,
   nominal_voltage_v,voltage_statutory_deviation_pct,
   rated_current_a,current_tolerance_pct,current_high_threshold_a,
   energy_target_kwh_today,target_efficiency_pct,
   thd_v_limit_pct,thd_i_limit_pct,true_pf_min,
   neutral_current_limit_a,frequency_hz,
   overview_* status thresholds
 },
 object:{slug,label,url,rating}}
```

## Observed endpoint summary

| Page | Endpoint |
|---|---|
| Overview | `/ws/mfm/1000026/overview/` |
| Overview | `/ws/mfm/1000026/power-energy-analysis/?range=last_7d` |
| Overview | `/api/equipment/key/tx-01/viewer/?default=panel` |
| Real Time Monitoring | `/ws/mfm/1000026/real-time-monitoring/` |
| Energy & Power | `/ws/mfm/1000026/energy-power/` |
| Energy & Power | `/ws/mfm/1000026/energy-summary/?range=today` |
| Energy & Power | `/ws/mfm/1000026/power-energy-analysis/?range=today&sampling=hour` |
| Energy & Power | `/ws/mfm/1000026/load-anomalies/?range=today&sampling=hour` |
| Voltage & Current | `/ws/mfm/1000026/voltage-current/` |
| Voltage & Current | `/ws/mfm/1000026/voltage-history/?range=today&sampling=hourly` |
| Voltage & Current | `/ws/mfm/1000026/current-history/?range=today&sampling=hourly` |
| Power Quality | `/ws/mfm/1000026/power-quality-summary/` |
| Power Quality | `/ws/mfm/1000026/power-quality-history/?range=today&sampling=2hour` |
| Shared page configuration | `/api/mfm/1000026/config/` |
