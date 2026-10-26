# PCC-1A Frontend Endpoints

This document lists the HTTP APIs and WebSockets actually used by the six
screens at:

```text
http://100.90.185.31:3107/electrical/pcc-panels/pcc-1a
```

The inventory was verified in two ways on 2026-09-21:

1. Two Playwright network captures while opening all six tabs.
2. A route-to-consumer check against `config/urls.py`, `core/urls.py`,
   `kit_preview/urls.py` and `panels/routing.py`.

Static JavaScript, CSS, fonts, images and `/media/*.glb` files are not included.
Only API and WebSocket endpoints are listed.

## Addressing

| Item | Value |
|---|---|
| Panel | PCC-1A |
| Equipment key | `pcc-1a` |
| Aggregate panel ID | `47` |
| HTTP origin | `http://100.90.185.31:3107` |
| WebSocket origin | `ws://100.90.185.31:3107` |

For panel aggregate sockets, `47` is the `Equipment.id`. It is not an individual
logger MFM ID. IDs below `1,000,000` are routed as distribution-panel equipment;
individual feeder sockets use `1,000,000 + MFM.id`.

## Complete page map

| Screen | HTTP APIs used | WebSocket used |
|---|---|---|
| Shared page/bootstrap | `/api/ems/`, `/api/bms/`, `/api/assets/`, `/api/kit-preview/catalog/`, `/api/mfm/47/config/`, `/api/equipment/key/pcc-1a/viewer/` | None |
| Overview | Shared APIs | `/ws/mfm/47/energy-distribution/` |
| Real-Time Monitoring | Shared APIs already loaded | `/ws/mfm/47/real-time-monitoring/` |
| Energy & Distribution | Shared APIs already loaded | `/ws/mfm/47/energy-distribution/` |
| Energy & Power | Shared APIs already loaded | `/ws/mfm/47/energy-power/?range=today&sampling=hour` |
| Voltage & Current | Shared APIs already loaded | `/ws/mfm/47/voltage-current/?range=today&sampling=hourly&start_date=2026-09-21&end_date=2026-09-21&days=1` |
| Harmonics & PQ | Shared APIs already loaded | `/ws/mfm/47/power-quality-summary/?range=today&sampling=hourly&start_date=2026-09-21&end_date=2026-09-21&days=1` |

The dates in the final two URLs are the selected UI dates from the capture. They
change when the user selects another day or custom range.

## Special time filter

Yes, the endpoint inventory includes the special time filter. It is not a
separate API or WebSocket. It changes the query parameters or sends a JSON
command over the page's existing WebSocket.

The full control shown on Voltage & Current and Harmonics & PQ is:

```text
Show <events/issues> for [range] by [sampling] at [selected bucket]
                         |           |                |
                       range      sampling       selected_period
```

### Meaning of each control

| UI control | Backend value | What it controls |
|---|---|---|
| `for` | `range` plus optional start/end dates | The complete source interval from which events and aggregates are calculated. |
| `by` | `sampling` | The size/alignment of each point or bar on the timeline. |
| `at` | selected bucket/time | The bucket inspected by detail widgets. This is different from the complete source interval. |
| Shift selector | `shift=all|a|b|c` | Keeps all shifts or one IST shift when shift sampling is active. |

The common logical formula is:

```text
[window_start, window_end) = resolve(range, custom_start, custom_end,
                                     latest_logger_timestamp, Asia/Kolkata)

timeline = aggregate(measurements within the window, sampling)

detail_window = selected_bucket
              ? [selected_bucket_start, selected_bucket_end)
              : [window_start, window_end)
```

The interval is start-inclusive and end-exclusive. A date-only custom range
includes the selected end date by resolving its exclusive end to midnight at
the start of the following IST day.

### Range and sampling structure

| Selection | Window/bucket behavior |
|---|---|
| `Today` | Start of the current IST day through the latest logger timestamp. |
| `Yesterday` | Previous IST day, midnight to midnight. |
| `This week` | Monday 00:00 IST through the latest logger timestamp. |
| `Last 7 days` | Seven-day lookback through the latest logger timestamp. |
| `This month` | First day of the current IST month through the latest logger timestamp. |
| `Last month` | Complete previous calendar month where supported. |
| `Custom range/date` | Explicit `start_date` and `end_date`, or the older `start` and `end` aliases. |
| `Hourly` | One-hour timeline buckets. |
| `Shift` | Eight-hour buckets: A 06:00-14:00, B 14:00-22:00, C 22:00-06:00 IST. |
| `Daily` or wider multi-day sampling | Day-aligned buckets for panel Voltage & Current and PQ; Energy & Power applies its own permitted range/sampling matrix. |

The browser also sends `days=1` on the Voltage & Current and Harmonics & PQ
default URLs. The current backend consumers do not read `days`; `range`,
`sampling` and the start/end fields are authoritative.

### Filter support by screen

| Screen | Time-filter behavior |
|---|---|
| Overview | No visible time filter. Its energy-distribution socket defaults to `today`. |
| Real-Time Monitoring | No calendar filter. It uses a rolling 900-second live queue. |
| Energy & Distribution | Range/custom-date filter and optional shift selection. Energy values are counter deltas for the selected interval. |
| Energy & Power | `range`, `sampling`, custom `start/end` and shift. The same filter is used by its four socket connections/widgets. |
| Voltage & Current | Full `for / by / at` behavior. Range and sampling are connection parameters; an explicit `at` choice sends `selected_period.bucket`. |
| Harmonics & PQ | Range/sampling/custom dates and shift are backend filters. The current consumer does not implement a historical `at` command. |

### Important `at` behavior

For Voltage & Current, selecting a timeline bucket sends:

```json
{"selected_period":{"bucket":"12:00"}}
```

The backend then recalculates the per-feeder detail table for that bucket's
start/end interval. Until such a command is received, `selected_bucket` is null
and the table is aggregated over the complete selected range, even if the UI
initially displays the latest time in its `at` control.

For Harmonics & PQ, the current `PowerQualityConsumer` accepts range/sampling,
shift, feeder selection and matrix focus, but it has no `selected_period` or
`timeline_time` handler. Its timeline is historical, while `pq_priority`, the
feeder table and signature inputs come from the latest live feeder readings.
Therefore an `at 12:00` label must not be interpreted as a backend historical
snapshot of those feeder rows under the current implementation.

## What is shown against what

### Overview

| UI area | What is shown | Compared/related against |
|---|---|---|
| SLD | Incomer, bus, outgoing feeder and breaker topology | Electrical source-to-bus-to-load relationships; this is topology, not a time-series axis. |
| Incoming/Outgoing toggles | Feeder visibility/state groups | Each feeder's role and status. |
| Bottom summary strip | Selected role-group energy, voltage, current, power and PF | Aggregated readings across the visible/selected group. |

The Overview uses `/energy-distribution/` for panel aggregate data and the
viewer/config APIs for presentation and nameplate references.

### Real-Time Monitoring

| UI area | Horizontal/category dimension | Values being compared |
|---|---|---|
| Main heatmap | Columns are metrics; rows are incomers and outgoing feeders | Active kW, reactive kVAr, apparent kVA, PF, voltage, current, current unbalance and voltage unbalance per feeder. |
| Cell color | Metric value | The metric's configured status band/threshold. |
| Outgoing consumed/incomer supplied | Outgoing total | Incomer supplied kW and remaining/excess balance. |
| Consumption trend | Time | Aggregated outgoing active power in kW. |
| Bus KPI cards | Outgoing feeder group | Peak load, PF, average bus voltage, worst current unbalance and total current. |

### Energy & Distribution

| UI area | What is shown | Compared/related against |
|---|---|---|
| Sources list | Each incomer's selected-window kWh | Total measured source input and its nameplate-derived utilization. |
| Consumers list | Each outgoing feeder's selected-window kWh | Total metered output and each feeder's percentage share. |
| Energy flow/Sankey | Source and consumer nodes | Link width represents selected-window kWh flowing from sources through PCC-1A to loads. |
| Efficiency ribbon | Measured source input kWh | Metered feeder output kWh; the difference is reconciliation loss or meter gap. |
| Utilization | Supplied/consumed energy | `energy / (rated_kVA x 0.9 x selected-window hours) x 100`. |

Changing `range` or `shift` recalculates both sides using the same interval, so
input and output remain directly comparable.

### Energy & Power

| UI area | X/category dimension | Y/value comparison |
|---|---|---|
| Cumulative Energy | Selected range | Active MWh, reactive MVArh and apparent MVAh against the configured MVAh limit/capacity. |
| Energy Consumption Trend | Time bucket from `range + sampling` | Active kWh and reactive kVArh, with feeder/group contribution data in each bucket. |
| Live Power Analysis | Current/live snapshot | Active kW and reactive kVAr against apparent kVA and rated kVA; load factor is a separate percentage. |
| Daily Power Demand by Feeder | Time bucket | Peak kW by feeder/group, including UPS, BPDB, HHF and incomers; worst peak and load factor summarize the selected range. |

Energy is compared in energy units over a period; demand and live power are
compared in power units. They should not be treated as the same measurement.

### Voltage & Current

| UI area | X/category dimension | Y/value comparison |
|---|---|---|
| Event summary strip | Selected complete range | Total sag, swell, current-unbalance and neutral-stress events; worst voltage deviation and current unbalance are range maxima. |
| Event Timeline | Time buckets from `for + by` | Left axis: counts of sag, swell, current and neutral events. Right axis: worst voltage-deviation % and current-unbalance % per bucket. |
| Current Distribution | Feeders around the radar | Average current in amperes for each feeder in the selected `at` bucket, or the full range before a bucket command. |
| Other Panels table | One row per incomer/outgoing feeder | Sag/swell/current events, voltage, voltage deviation, current, current unbalance and status for the selected detail interval. |

Event thresholds currently include voltage sag below `-10%`, swell above
`+10%`, and current unbalance above `10%`. Per-row warning/danger styling uses
the panel's configured voltage-deviation and current-unbalance thresholds.

### Harmonics & PQ

| UI area | X/category dimension | Y/value comparison |
|---|---|---|
| Issue summary strip | Selected complete range | Counts of I-THD, V-THD, PF-gap and neutral-stress issues; worst I-THD and V-THD are range maxima. |
| Harmonics & PQ Timeline | Time buckets from `for + by` | Left axis: issue counts for I-THD, V-THD, PF gap and neutral stress. Right axis: worst I-THD % and V-THD % per bucket. |
| Feeder PQ table | One row per incomer/outgoing feeder | Latest I-THD, V-THD, PF, peak I-THD and dominant driver. These rows are not currently reconstructed for the selected `at` time. |
| Signature/radar | PQ metric axes | Selected feeder versus fleet-average I-THD, V-THD, H5, H7, K-factor and PF gap. |
| Severity/ranking | Each feeder's PQ score | Panel-specific I-THD, V-THD, true-PF and neutral-current limits. |

This separation matters: the PQ timeline answers **when issues occurred**, while
the current feeder table/signature answers **which feeder has the strongest
latest PQ concern**.

## HTTP APIs

All observed HTTP calls use `GET` and have no request body.

### `GET /api/ems/`

Shared EMS navigation tree. It supplies the panel hierarchy, role groups and
frontend IDs used to navigate to PCC-1A and its feeders.

### `GET /api/bms/`

Shared BMS navigation tree loaded by the application shell. It is not a PCC-1A
measurement endpoint, but it is requested when this page starts.

### `GET /api/assets/`

Shared asset navigation tree loaded by the application shell. It is not a
PCC-1A measurement endpoint.

### `GET /api/kit-preview/catalog/`

Shared 3D/model catalog used by the viewer infrastructure.

### `GET /api/mfm/47/config/`

Resolved panel configuration used across the screens. It supplies panel
nameplate and operating references such as rated capacity, nominal voltage,
current limits, energy/subsidy references and PQ/status thresholds.

Despite the URL segment `mfm`, `47` resolves to the PCC-1A `Equipment` because
it is an aggregate panel ID.

### `GET /api/equipment/key/pcc-1a/viewer/`

Resolved viewer payload for PCC-1A. It supplies:

```text
equipment
object
viewer
template
```

The object's model URL may cause a later `/media/...glb` file request. That is a
static media request, not another API endpoint.

## WebSockets

Every panel socket uses the aggregate ID `47`. Its normal telemetry frames use
the common aggregate envelope:

```jsonc
{
  "type": "snapshot" | "tick",
  "mfm_id": 47,
  "mfm_name": "PCC-1A",
  "panel_id": "pcc-1a",
  "mfm_type": "pcc_panel",
  "page": "<page>",
  "ts": "<ISO timestamp>",
  "widgets": {}
}
```

Some consumers subsequently emit a separate `type: "ai"` frame containing
`mfm_id`, `page`, `param_sig`, `summary`, `source`, `model` and `latency_ms`.
That auxiliary frame does not contain the normal `widgets` envelope.

The initial WebSocket connection has no body. Filters are supplied in the URL
query string. Later UI interactions may send JSON command frames over the same
connection.

### Overview and Energy & Distribution

```text
ws://100.90.185.31:3107/ws/mfm/47/energy-distribution/
```

This single endpoint is used by both screens. The Overview does not open
`/ws/mfm/47/overview/`; panel Overview is driven by the energy-distribution
aggregate plus viewer/config data.

Optional connection parameters:

```text
range=today|yesterday|this_week|this_month|last_24h|last_7d|custom
start=<ISO date or datetime>
end=<ISO date or datetime>
```

Supported command structure:

```jsonc
{
  "range": "today",
  "start": "2026-09-21",
  "end": "2026-09-21",
  "shift": "all" // all | a | b | c
}
```

The socket supplies the input/distribution totals, incomers, consumers, energy
flow/Sankey data, meter gap, efficiency and related summary values.

### Real-Time Monitoring

```text
ws://100.90.185.31:3107/ws/mfm/47/real-time-monitoring/
```

No connection query parameters were used. The socket supplies the live feeder
heatmap, rolling queues and outgoing-bus summary.

Supported commands:

```jsonc
{"select_feeder": 31}
{"clear_feeder": true}
```

### Energy & Power

Observed default URL:

```text
ws://100.90.185.31:3107/ws/mfm/47/energy-power/?range=today&sampling=hour
```

Supported connection/filter fields:

```text
range
sampling
start
end
shift
```

The same fields can be sent as a command to rebuild the socket result:

```jsonc
{
  "range": "custom",
  "sampling": "day",
  "start": "2026-09-01",
  "end": "2026-09-21",
  "shift": "all"
}
```

It supplies all four visible areas: Cumulative Energy, Energy Consumption
Trend, Live Power Analysis and Daily Power Demand by Feeder.

The current frontend opened this identical URL **four times concurrently** when
the Energy & Power tab was selected. This is one unique endpoint but four
physical WebSocket connections, apparently one per widget.

### Voltage & Current

Observed default URL:

```text
ws://100.90.185.31:3107/ws/mfm/47/voltage-current/?range=today&sampling=hourly&start_date=2026-09-21&end_date=2026-09-21&days=1
```

Connection parameters:

```text
range
sampling
shift
start_date / end_date
start / end                 # older aliases also accepted
days                        # sent by the frontend; not used by the consumer
```

Commands select a shift, timeline bucket or feeder row over the existing
connection:

```jsonc
{"shift": "a"}
{"selected_period": {"bucket": "12:00"}}
{"selected_panel": {"mfm_id": 31}}
```

The socket supplies the event strip, voltage/current event timeline, current
distribution and per-feeder table.

### Harmonics & PQ

Observed default URL:

```text
ws://100.90.185.31:3107/ws/mfm/47/power-quality-summary/?range=today&sampling=hourly&start_date=2026-09-21&end_date=2026-09-21&days=1
```

Connection parameters:

```text
range
sampling
shift
start_date
end_date
days                        # sent by the frontend; not used by the consumer
```

Supported commands:

```jsonc
{"select_feeder": 28}
{"fleet_matrix": {"focus": "i_thd"}}
{
  "timeline_filter": {
    "range": "custom_range",
    "sampling": "daily",
    "shift": "all",
    "start_date": "2026-09-01",
    "end_date": "2026-09-21"
  }
}
{"shift": "b"}
```

The socket supplies the issue strip, Harmonics & PQ timeline, feeder PQ table,
selected-feeder signature and AI summary input.

## Unique endpoint summary

### HTTP: 6 unique GET endpoints

```text
/api/ems/
/api/bms/
/api/assets/
/api/kit-preview/catalog/
/api/mfm/47/config/
/api/equipment/key/pcc-1a/viewer/
```

### WebSocket: 5 unique endpoints

```text
/ws/mfm/47/energy-distribution/
/ws/mfm/47/real-time-monitoring/
/ws/mfm/47/energy-power/
/ws/mfm/47/voltage-current/
/ws/mfm/47/power-quality-summary/
```

Therefore these screens currently use **11 unique API/WebSocket endpoints**:
six HTTP APIs and five WebSockets.

If the previously identified common tree APIs (`/api/ems/`, `/api/bms/` and
`/api/assets/`) are excluded, the PCC-1A page-specific count is **8 unique
endpoints**: three HTTP APIs and five WebSockets.

## Registered but not used by these screens

The backend also registers the following panel-capable sockets, but neither
Playwright capture opened them for the supplied PCC-1A screens:

```text
/ws/mfm/47/energy-rail/
/ws/mfm/47/energy-flow/
/ws/mfm/47/efficiency-ribbon/
/ws/mfm/47/event-timeline/
/ws/mfm/47/energy-summary/
/ws/mfm/47/power-summary/
/ws/mfm/47/power-demand/
/ws/mfm/47/power-energy-analysis/
/ws/mfm/47/load-anomalies/
```

They must not be counted as frontend dependencies for the current screen
implementation merely because matching backend routes exist.
