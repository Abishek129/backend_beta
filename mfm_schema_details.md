# LoggerFast schemas and fields

_Generated on 2026-09-21 08:48 IST directly from the live LoggerFast metadata database._

This document uses `app_schemas` and `app_schema_fields` as its source of truth. Workbooks and repository reference files are not used.

## Summary

| Schema | Internal ID | Total fields | Measured | Derived |
|---|---|---:|---:|---:|
| `sch_DG` | `sch_1789555418173` | 45 | 32 | 13 |
| `sch_EM6400_NG+` | `sch_1788521250508` | 73 | 43 | 30 |
| `sch_EMMS7_96` | `sch_1788773139707` | 66 | 36 | 30 |
| `sch_LG2599` | `sch_1789627016559` | 55 | 27 | 28 |

## Field definitions

### `sch_DG`

Internal ID: `sch_1789555418173`  
Fields: **45 total** · **32 measured** · **13 derived**

| # | Field | Kind | Data type | Unit | Address | Scale | Formula | Eval order | Description |
|---:|---|---|---|---|---|---:|---|---:|---|
| 1 | `active_energy_export_kwh` | Measured | `uint16` | kWh | `40046` | 1 | — | — | — |
| 2 | `active_energy_import_kwh` | Measured | `uint16` | kWh | `40048` | 1 | — | — | — |
| 3 | `active_energy_net_kwh` | Measured | `int16` | kWh | `40050` | 1 | — | — | — |
| 4 | `aftercooler_temperature_c` | Derived | `float64` | Celsius | — | 1 | `(aftercooler_temperature_f - 32) / 1.8` | 0 | Derived parameter |
| 5 | `aftercooler_temperature_f` | Measured | `int16` | — | `40242` | — | — | — | — |
| 6 | `alternator_temp_1_c` | Derived | `float64` | Celsius | — | 1 | `(alternator_temp_1_f - 32) / 1.8` | 1 | Derived parameter |
| 7 | `alternator_temp_1_f` | Measured | `uint16` | — | `43719` | 0.1 | — | — | — |
| 8 | `alternator_temp_2_c` | Derived | `float64` | Celsius | — | 1 | `(alternator_temp_2_f - 32) / 1.8` | 2 | Derived parameter |
| 9 | `alternator_temp_2_f` | Measured | `uint16` | — | `43720` | 0.1 | — | — | — |
| 10 | `alternator_temp_3_c` | Derived | `float64` | Celsius | — | 1 | `(alternator_temp_3_f - 32) / 1.8` | 3 | Derived parameter |
| 11 | `alternator_temp_3_f` | Measured | `uint16` | — | `43721` | 0.1 | — | — | — |
| 12 | `ambient_pressure_kpa` | Measured | `uint16` | hPa | `40229` | 0.1 | — | — | — |
| 13 | `ambient_temperature_c` | Derived | `float64` | Celsius | — | 1 | `(ambient_temperature_f - 32) / 1.8` | 4 | Derived parameter |
| 14 | `ambient_temperature_f` | Measured | `uint16` | — | `43722` | 0.1 | — | — | — |
| 15 | `battery_voltage_v` | Measured | `uint32` | Volt | `40061` | 0.1 | — | — | — |
| 16 | `boost_pressure_kpa` | Measured | `uint16` | — | `40230` | 0.1 | — | — | — |
| 17 | `coolant_pressure_psi` | Measured | `uint32` | — | `46124` | 1 | — | — | — |
| 18 | `coolant_temperature_c` | Measured | `int16` | Celsius | `40064` | 0.1 | — | — | — |
| 19 | `crankcase_pressure_kpa` | Measured | `uint16` | hPa | `40232` | 0.1 | — | — | — |
| 20 | `engine_no_of_starts` | Measured | `uint32` | — | `40069` | 1 | — | — | — |
| 21 | `engine_run_time_hr` | Derived | `float64` | hr | — | 1 | `engine_run_time_s / 3600` | 5 | Derived parameter |
| 22 | `engine_run_time_s` | Measured | `uint32` | — | `40070` | 0.1 | — | — | — |
| 23 | `engine_speed_rpm` | Measured | `uint16` | — | `40068` | 1 | — | — | — |
| 24 | `engine_torque_pct` | Measured | `int32` | — | `40264` | 1 | — | — | — |
| 25 | `exhaust_temp_1_c` | Derived | `float64` | Celsius | — | 1 | `(exhaust_temp_1_f - 32) / 1.8` | 6 | Derived parameter |
| 26 | `exhaust_temp_1_f` | Measured | `uint16` | — | `43741` | 0.1 | — | — | — |
| 27 | `exhaust_temp_2_c` | Derived | `float64` | Celsius | — | 1 | `(exhaust_temp_2_f - 32) / 1.8` | 7 | Derived parameter |
| 28 | `exhaust_temp_2_f` | Measured | `uint16` | — | `43742` | 0.1 | — | — | — |
| 29 | `fuel_level_pct` | Measured | `uint32` | — | `43745` | 1 | — | — | — |
| 30 | `fuel_rate_gph` | Measured | `uint32` | — | `40067` | 0.05 | — | — | — |
| 31 | `fuel_rate_lph` | Derived | `float64` | L/h | — | 1 | `fuel_rate_gph * 3.785412` | 8 | Derived parameter |
| 32 | `fuel_supply_pressure_psi` | Measured | `uint32` | — | `40254` | 0.1 | — | — | — |
| 33 | `fuel_temperature_c` | Derived | `float64` | Celsius | — | 1 | `(fuel_temperature_f - 32) / 1.8` | 9 | Derived parameter |
| 34 | `fuel_temperature_f` | Measured | `int16` | — | `40066` | 0.1 | — | — | — |
| 35 | `intake_manifold_temp_c` | Derived | `float64` | Celsius | — | 1 | `(intake_manifold_temp_f - 32) / 1.8` | 10 | Derived parameter |
| 36 | `intake_manifold_temp_f` | Measured | `int16` | — | `40065` | 0.1 | — | — | — |
| 37 | `oil_pressure_kpa` | Measured | `uint16` | hPa | `40062` | 1 | — | — | — |
| 38 | `oil_temperature_c` | Derived | `float64` | Celsius | — | 1 | `(oil_temperature_f - 32) / 1.8` | 11 | Derived parameter |
| 39 | `oil_temperature_f` | Measured | `int16` | Celsius | `40063` | 0.1 | — | — | — |
| 40 | `reactive_energy_export_kvarh` | Measured | `uint16` | kVArh | `40052` | 1 | — | — | — |
| 41 | `reactive_energy_import_kvarh` | Measured | `uint16` | kVArh | `40054` | 1 | — | — | — |
| 42 | `reactive_energy_net_kvarh` | Measured | `int16` | kVArh | `40056` | 1 | — | — | — |
| 43 | `total_fuel_consumption_gal` | Measured | `uint16` | — | `40072` | 0.1 | — | — | — |
| 44 | `total_fuel_consumption_l` | Derived | `float64` | L | — | 1 | `total_fuel_consumption_gal * 3.785412` | 12 | Derived parameter |
| 45 | `total_number_of_runs` | Measured | `uint16` | — | `40074` | 1 | — | — | — |

### `sch_EM6400_NG+`

Internal ID: `sch_1788521250508`  
Fields: **73 total** · **43 measured** · **30 derived**

| # | Field | Kind | Data type | Unit | Address | Scale | Formula | Eval order | Description |
|---:|---|---|---|---|---|---:|---|---:|---|
| 1 | `active_energy_import_kwh` | Measured | `float` | kWh | `42700` | 1 | — | — | desc |
| 2 | `active_power_b_kw` | Measured | `float` | kW | `43058` | 1 | — | — | desc |
| 3 | `active_power_r_kw` | Measured | `float` | kW | `43054` | 1 | — | — | desc |
| 4 | `active_power_total_kw` | Measured | `float` | kW | `43050` | 1 | — | — | desc |
| 5 | `active_power_y_kw` | Measured | `float` | kW | `43056` | 1 | — | — | desc |
| 6 | `apparent_energy_import_kvah` | Measured | `float` | kVAh | `42716` | 1 | — | — | desc |
| 7 | `apparent_power_b_kva` | Measured | `float` | kVA | `43074` | 1 | — | — | desc |
| 8 | `apparent_power_r_kva` | Measured | `float` | kVA | `43070` | 1 | — | — | desc |
| 9 | `apparent_power_total_kva` | Measured | `float` | kVA | `43076` | 1 | — | — | desc |
| 10 | `apparent_power_y_kva` | Measured | `float` | kVA | `43072` | 1 | — | — | desc |
| 11 | `current_avg` | Measured | `float` | Ampere | `43010` | 1 | — | — | desc |
| 12 | `current_b` | Measured | `float` | Ampere | `43004` | 1 | — | — | desc |
| 13 | `current_b_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_b - current_avg) / current_avg * 100` | 0 | Derived parameter |
| 14 | `current_imbalance_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(current_unbalance_pct - 10))` | 10 | Derived parameter |
| 15 | `current_max` | Derived | `float64` | A | — | 1 | `max(current_r, current_y, current_b)` | 1 | Derived parameter |
| 16 | `current_max_spread` | Derived | `float64` | A | — | 1 | `max(current_r, current_y, current_b) - min(current_r, current_y, current_b)` | 2 | Derived parameter |
| 17 | `current_min` | Derived | `float64` | A | — | 1 | `min(current_r, current_y, current_b)` | 3 | Derived parameter |
| 18 | `current_neutral` | Derived | `float64` | A | — | 1 | `sqrt(current_r^2 + current_y^2 + current_b^2 - current_r*current_y - current_y*current_b - current_b*current_r)` | 4 | Derived parameter |
| 19 | `current_r` | Measured | `float` | Ampere | `43000` | 1 | — | — | desc |
| 20 | `current_r_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_r - current_avg) / current_avg * 100` | 5 | Derived parameter |
| 21 | `current_spread_br` | Derived | `float64` | A | — | 1 | `abs(current_b - current_r)` | 6 | Derived parameter |
| 22 | `current_spread_by` | Derived | `float64` | A | — | 1 | `abs(current_b - current_y)` | 7 | Derived parameter |
| 23 | `current_spread_ry` | Derived | `float64` | A | — | 1 | `abs(current_r - current_y)` | 8 | Derived parameter |
| 24 | `current_unbalance_pct` | Derived | `float64` | % | — | 1 | `max(abs(current_r - current_avg), abs(current_y - current_avg), abs(current_b - current_avg)) / current_avg * 100` | 9 | Derived parameter |
| 25 | `current_y` | Measured | `float` | Ampere | `43002` | 1 | — | — | desc |
| 26 | `current_y_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_y - current_avg) / current_avg * 100` | 11 | Derived parameter |
| 27 | `frequency_hz` | Measured | `float` | Hz | `43110` | 1 | — | — | desc |
| 28 | `kpi_displacement_pf` | Derived | `float64` | PF | — | 1 | `power_factor_total` | 12 | Derived parameter |
| 29 | `kpi_neutral_to_phase_ratio_pct` | Derived | `float64` | % | — | 1 | `current_neutral / current_avg * 100` | 13 | Derived parameter |
| 30 | `kpi_true_pf` | Derived | `float64` | PF | — | 1 | `abs(active_power_total_kw) / apparent_power_total_kva` | 14 | Derived parameter |
| 31 | `kpi_voltage_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_avg - 240) / 240 * 100` | 15 | Derived parameter |
| 32 | `neutral_stress_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(kpi_neutral_to_phase_ratio_pct - 20))` | 16 | Derived parameter |
| 33 | `peak_demand_kVA` | Measured | `float` | kVA | `43802` | 1 | — | — | desc |
| 34 | `peak_demand_kVAr` | Measured | `float` | kVAR | `43786` | 1 | — | — | desc |
| 35 | `peak_demand_kw` | Measured | `float` | kW | `43770` | 1 | — | — | desc |
| 36 | `pf_gap_vs_full_load` | Derived | `float64` | PF | — | 1 | `kpi_displacement_pf - kpi_true_pf` | 17 | Derived parameter |
| 37 | `phase_angle_deg` | Derived | `float64` | ° | — | 1 | `acos(max(-1, min(1, power_factor_total))) * 180 / pi` | 18 | Derived parameter |
| 38 | `power_factor_b` | Measured | `float` | PF | `43082` | 1 | — | — | desc |
| 39 | `power_factor_r` | Measured | `float` | PF | `43078` | 1 | — | — | desc |
| 40 | `power_factor_total` | Measured | `float` | PF | `43084` | 1 | — | — | desc |
| 41 | `power_factor_y` | Measured | `float` | PF | `43080` | 1 | — | — | desc |
| 42 | `present_demand_kVA` | Measured | `float` | kVA | `43082` | 1 | — | — | desc |
| 43 | `present_demand_kVAr` | Measured | `float` | kVAR | `43786` | 1 | — | — | desc |
| 44 | `present_demand_kw` | Measured | `float` | kW | `43766` | 1 | — | — | desc |
| 45 | `reactive_energy_import_kvarh` | Measured | `float` | kVArh | `42708` | 1 | — | — | desc |
| 46 | `reactive_power_b_kvar` | Measured | `float` | kVAR | `43066` | 1 | — | — | desc |
| 47 | `reactive_power_r_kvar` | Measured | `float` | kVAR | `43062` | 1 | — | — | desc |
| 48 | `reactive_power_total_kvar` | Measured | `float` | kVAR | `43068` | 1 | — | — | desc |
| 49 | `reactive_power_y_kvar` | Measured | `float` | kVAR | `43064` | 1 | — | — | desc |
| 50 | `sag_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(-10 - kpi_voltage_deviation_pct))` | 19 | Derived parameter |
| 51 | `swell_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(kpi_voltage_deviation_pct - 10))` | 20 | Derived parameter |
| 52 | `thd_compliance_i_avg` | Derived | `float64` | % | — | 1 | `(thd_current_r_pct + thd_current_y_pct + thd_current_b_pct) / 3` | 21 | Derived parameter |
| 53 | `thd_compliance_ieee519` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(5 - thd_compliance_v_avg)) * max(0, signum(8 - thd_compliance_i_avg))` | 23 | Derived parameter |
| 54 | `thd_compliance_v_avg` | Derived | `float64` | % | — | 1 | `(thd_voltage_r_pct + thd_voltage_y_pct + thd_voltage_b_pct) / 3` | 22 | Derived parameter |
| 55 | `thd_current_b_pct` | Measured | `float` | % | `421304` | 1 | — | — | desc |
| 56 | `thd_current_r_pct` | Measured | `float` | % | `421300` | 1 | — | — | desc |
| 57 | `thd_current_y_pct` | Measured | `float` | % | `421302` | 1 | — | — | desc |
| 58 | `thd_voltage_b_pct` | Measured | `float` | % | `421334` | 1 | — | — | desc |
| 59 | `thd_voltage_r_pct` | Measured | `float` | kWh | `421330` | 1 | — | — | desc |
| 60 | `thd_voltage_y_pct` | Measured | `float` | kWh | `421332` | 1 | — | — | desc |
| 61 | `voltage_avg` | Measured | `float` | Volt | `43026` | 1 | — | — | desc |
| 62 | `voltage_b_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_b_n - 240) / 240 * 100` | 24 | Derived parameter |
| 63 | `voltage_b_n` | Measured | `float` | Volt | `43032` | 1 | — | — | desc |
| 64 | `voltage_br` | Measured | `float` | Volt | `43024` | 1 | — | — | desc |
| 65 | `voltage_max` | Derived | `float64` | V | — | 1 | `max(voltage_r_n, voltage_y_n, voltage_b_n)` | 25 | Derived parameter |
| 66 | `voltage_min` | Derived | `float64` | V | — | 1 | `min(voltage_r_n, voltage_y_n, voltage_b_n)` | 26 | Derived parameter |
| 67 | `voltage_r_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_r_n - 240) / 240 * 100` | 27 | Derived parameter |
| 68 | `voltage_r_n` | Measured | `float` | Volt | `43028` | 1 | — | — | desc |
| 69 | `voltage_ry` | Measured | `float` | Volt | `43020` | 1 | — | — | desc |
| 70 | `voltage_unbalance_pct` | Derived | `float64` | % | — | 1 | `max(abs(voltage_r_n - voltage_avg), abs(voltage_y_n - voltage_avg), abs(voltage_b_n - voltage_avg)) / voltage_avg * 100` | 28 | Derived parameter |
| 71 | `voltage_yb` | Measured | `float` | Volt | `43022` | 1 | — | — | desc |
| 72 | `voltage_y_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_y_n - 240) / 240 * 100` | 29 | Derived parameter |
| 73 | `voltage_y_n` | Measured | `float` | Volt | `43030` | 1 | — | — | desc |

### `sch_EMMS7_96`

Internal ID: `sch_1788773139707`  
Fields: **66 total** · **36 measured** · **30 derived**

| # | Field | Kind | Data type | Unit | Address | Scale | Formula | Eval order | Description |
|---:|---|---|---|---|---|---:|---|---:|---|
| 1 | `active_energy_import_kWh` | Measured | `int32` | kWh | `41409` | 1 | — | — | desc |
| 2 | `active_power_b_kw` | Measured | `int16` | kW | `40794` | 0.1 | — | — | desc |
| 3 | `active_power_r_kw` | Measured | `int16` | kW | `40792` | 0.1 | — | — | desc |
| 4 | `active_power_total_kw` | Measured | `int16` | kW | `40795` | 0.1 | — | — | desc |
| 5 | `active_power_y_kw` | Measured | `int16` | kW | `40793` | 0.1 | — | — | desc |
| 6 | `apparent_energy_kVAh` | Measured | `int32` | kVAh | `41421` | 1 | — | — | desc |
| 7 | `apparent_power_b_kva` | Measured | `int16` | kVA | `40806` | 0.1 | — | — | desc |
| 8 | `apparent_power_r_kva` | Measured | `int16` | kVA | `40804` | 0.1 | — | — | desc |
| 9 | `apparent_power_total_kva` | Measured | `int16` | kVA | `40807` | 0.1 | — | — | desc |
| 10 | `apparent_power_y_kva` | Measured | `int16` | kVA | `40805` | 0.1 | — | — | desc |
| 11 | `current_avg` | Measured | `int16` | Ampere | `40773` | 1 | — | — | desc |
| 12 | `current_b` | Measured | `int16` | Ampere | `40771` | 1 | — | — | desc |
| 13 | `current_b_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_b - current_avg) / current_avg * 100` | 0 | Derived parameter |
| 14 | `current_imbalance_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(current_unbalance_pct - 10))` | 10 | Derived parameter |
| 15 | `current_max` | Derived | `float64` | A | — | 1 | `max(current_r, current_y, current_b)` | 1 | Derived parameter |
| 16 | `current_max_spread` | Derived | `float64` | A | — | 1 | `max(current_r, current_y, current_b) - min(current_r, current_y, current_b)` | 2 | Derived parameter |
| 17 | `current_min` | Derived | `float64` | A | — | 1 | `min(current_r, current_y, current_b)` | 3 | Derived parameter |
| 18 | `current_neutral` | Derived | `float64` | A | — | 1 | `sqrt(current_r^2 + current_y^2 + current_b^2 - current_r*current_y - current_y*current_b - current_b*current_r)` | 4 | Derived parameter |
| 19 | `current_r` | Measured | `int16` | Ampere | `40769` | 1 | — | — | desc |
| 20 | `current_r_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_r - current_avg) / current_avg * 100` | 5 | Derived parameter |
| 21 | `current_spread_br` | Derived | `float64` | A | — | 1 | `abs(current_b - current_r)` | 6 | Derived parameter |
| 22 | `current_spread_by` | Derived | `float64` | A | — | 1 | `abs(current_b - current_y)` | 7 | Derived parameter |
| 23 | `current_spread_ry` | Derived | `float64` | A | — | 1 | `abs(current_r - current_y)` | 8 | Derived parameter |
| 24 | `current_unbalance_pct` | Derived | `float64` | % | — | 1 | `max(abs(current_r - current_avg), abs(current_y - current_avg), abs(current_b - current_avg)) / current_avg * 100` | 9 | Derived parameter |
| 25 | `current_y` | Measured | `int16` | Ampere | `40770` | 1 | — | — | desc |
| 26 | `current_y_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_y - current_avg) / current_avg * 100` | 11 | Derived parameter |
| 27 | `frequency_hz` | Measured | `int16` | Hz | `40791` | 0.1 | — | — | desc |
| 28 | `kpi_displacement_pf` | Derived | `float64` | PF | — | 1 | `power_factor_total` | 12 | Derived parameter |
| 29 | `kpi_neutral_to_phase_ratio_pct` | Derived | `float64` | % | — | 1 | `current_neutral / current_avg * 100` | 13 | Derived parameter |
| 30 | `kpi_true_pf` | Derived | `float64` | PF | — | 1 | `abs(active_power_total_kw) / apparent_power_total_kva` | 14 | Derived parameter |
| 31 | `kpi_voltage_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_avg - 240) / 240 * 100` | 15 | Derived parameter |
| 32 | `neutral_stress_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(kpi_neutral_to_phase_ratio_pct - 20))` | 16 | Derived parameter |
| 33 | `pf_gap_vs_full_load` | Derived | `float64` | PF | — | 1 | `kpi_displacement_pf - kpi_true_pf` | 17 | Derived parameter |
| 34 | `phase_angle_deg` | Derived | `float64` | ° | — | 1 | `acos(max(-1, min(1, power_factor_total))) * 180 / pi` | 18 | Derived parameter |
| 35 | `power_factor_b` | Measured | `int16` | PF | `40789` | 0.001 | — | — | desc |
| 36 | `power_factor_r` | Measured | `int16` | PF | `40787` | 0.001 | — | — | desc |
| 37 | `power_factor_total` | Measured | `int16` | PF | `40790` | 0.001 | — | — | desc |
| 38 | `power_factor_y` | Measured | `int16` | PF | `40788` | 0.001 | — | — | desc |
| 39 | `reactive_energy_import_kVArh` | Measured | `int32` | kVArh | `41413` | 1 | — | — | desc |
| 40 | `reactive_power_b_kvar` | Measured | `int16` | kVAR | `40802` | 0.1 | — | — | desc |
| 41 | `reactive_power_r_kvar` | Measured | `int16` | kVAR | `40800` | 0.1 | — | — | desc |
| 42 | `reactive_power_total_kvar` | Measured | `int16` | kVAR | `40803` | 0.1 | — | — | desc |
| 43 | `reactive_power_y_kvar` | Measured | `int16` | kVAR | `40801` | 0.1 | — | — | desc |
| 44 | `sag_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(-10 - kpi_voltage_deviation_pct))` | 19 | Derived parameter |
| 45 | `swell_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(kpi_voltage_deviation_pct - 10))` | 20 | Derived parameter |
| 46 | `thd_compliance_i_avg` | Derived | `float64` | % | — | 1 | `(thd_current_r_pct + thd_current_y_pct + thd_current_b_pct) / 3` | 21 | Derived parameter |
| 47 | `thd_compliance_ieee519` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(5 - thd_compliance_v_avg)) * max(0, signum(8 - thd_compliance_i_avg))` | 23 | Derived parameter |
| 48 | `thd_compliance_v_avg` | Derived | `float64` | % | — | 1 | `(thd_voltage_r_pct + thd_voltage_y_pct) / 2` | 22 | Derived parameter |
| 49 | `thd_current_b_pct` | Measured | `int16` | % | `42307` | 1 | — | — | desc |
| 50 | `thd_current_r_pct` | Measured | `int16` | % | `42305` | 1 | — | — | desc |
| 51 | `thd_current_y_pct` | Measured | `int16` | % | `42306` | 1 | — | — | desc |
| 52 | `thd_voltage_r_pct` | Measured | `int16` | kWh | `41793` | 1 | — | — | desc |
| 53 | `thd_voltage_y_pct` | Measured | `int16` | kWh | `41794` | 1 | — | — | desc |
| 54 | `voltage_avg` | Measured | `int16` | Volt | `40782` | 0.1 | — | — | desc |
| 55 | `voltage_b_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_b_n - 240) / 240 * 100` | 24 | Derived parameter |
| 56 | `voltage_b_n` | Measured | `int16` | Volt | `40785` | 0.1 | — | — | desc |
| 57 | `voltage_br` | Measured | `int16` | Volt | `40781` | 0.1 | — | — | desc |
| 58 | `voltage_max` | Derived | `float64` | V | — | 1 | `max(voltage_r_n, voltage_y_n, voltage_b_n)` | 25 | Derived parameter |
| 59 | `voltage_min` | Derived | `float64` | V | — | 1 | `min(voltage_r_n, voltage_y_n, voltage_b_n)` | 26 | Derived parameter |
| 60 | `voltage_r_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_r_n - 240) / 240 * 100` | 27 | Derived parameter |
| 61 | `voltage_r_n` | Measured | `int16` | Volt | `40783` | 0.1 | — | — | desc |
| 62 | `voltage_ry` | Measured | `int16` | Volt | `40779` | 0.1 | — | — | desc |
| 63 | `voltage_unbalance_pct` | Derived | `float64` | % | — | 1 | `max(abs(voltage_r_n - voltage_avg), abs(voltage_y_n - voltage_avg), abs(voltage_b_n - voltage_avg)) / voltage_avg * 100` | 28 | Derived parameter |
| 64 | `voltage_yb` | Measured | `int16` | Volt | `40780` | 0.1 | — | — | desc |
| 65 | `voltage_y_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_y_n - 240) / 240 * 100` | 29 | Derived parameter |
| 66 | `voltage_y_n` | Measured | `int16` | Volt | `40784` | 0.1 | — | — | desc |

### `sch_LG2599`

Internal ID: `sch_1789627016559`  
Fields: **55 total** · **27 measured** · **28 derived**

| # | Field | Kind | Data type | Unit | Address | Scale | Formula | Eval order | Description |
|---:|---|---|---|---|---|---:|---|---:|---|
| 1 | `active_energy_import_kWh` | Measured | `float` | kWh | `40159` | 1 | — | — | — |
| 2 | `active_power_b_kw` | Measured | `float` | kW | `40107` | 0.01 | — | — | — |
| 3 | `active_power_r_kw` | Measured | `float` | kW | `40103` | 1 | — | — | — |
| 4 | `active_power_total_kw` | Measured | `float` | kW | `40101` | 1 | — | — | — |
| 5 | `active_power_y_kw` | Measured | `float` | kW | `40105` | 1 | — | — | — |
| 6 | `apparent_energy_kVAh` | Measured | `float` | kVAh | `40161` | 1 | — | — | — |
| 7 | `apparent_power_b_kva` | Measured | `float` | kVA | `40131` | 1 | — | — | — |
| 8 | `apparent_power_r_kva` | Measured | `float` | kVA | `40127` | 1 | — | — | — |
| 9 | `apparent_power_total_kva` | Measured | `float` | PF | `40125` | 0.01 | — | — | — |
| 10 | `apparent_power_y_kva` | Measured | `float` | kVA | `40129` | 1 | — | — | — |
| 11 | `current_avg` | Measured | `float` | Ampere | `40149` | 1 | — | — | — |
| 12 | `current_b` | Measured | `float` | Ampere | `40155` | 1 | — | — | — |
| 13 | `current_b_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_b - current_avg) / current_avg * 100` | 0 | Derived parameter |
| 14 | `current_imbalance_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(current_unbalance_pct - 10))` | 10 | Derived parameter |
| 15 | `current_max` | Derived | `float64` | A | — | 1 | `max(current_r, current_y, current_b)` | 1 | Derived parameter |
| 16 | `current_max_spread` | Derived | `float64` | A | — | 1 | `max(current_r, current_y, current_b) - min(current_r, current_y, current_b)` | 2 | Derived parameter |
| 17 | `current_min` | Derived | `float64` | A | — | 1 | `min(current_r, current_y, current_b)` | 3 | Derived parameter |
| 18 | `current_neutral` | Derived | `float64` | A | — | 1 | `sqrt(current_r^2 + current_y^2 + current_b^2 - current_r*current_y - current_y*current_b - current_b*current_r)` | 4 | Derived parameter |
| 19 | `current_r` | Measured | `float` | Ampere | `40151` | 1 | — | — | — |
| 20 | `current_r_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_r - current_avg) / current_avg * 100` | 5 | Derived parameter |
| 21 | `current_spread_br` | Derived | `float64` | A | — | 1 | `abs(current_b - current_r)` | 6 | Derived parameter |
| 22 | `current_spread_by` | Derived | `float64` | A | — | 1 | `abs(current_b - current_y)` | 7 | Derived parameter |
| 23 | `current_spread_ry` | Derived | `float64` | A | — | 1 | `abs(current_r - current_y)` | 8 | Derived parameter |
| 24 | `current_unbalance_pct` | Derived | `float64` | % | — | 1 | `max(abs(current_r - current_avg), abs(current_y - current_avg), abs(current_b - current_avg)) / current_avg * 100` | 9 | Derived parameter |
| 25 | `current_y` | Measured | `float` | Ampere | `40153` | 1 | — | — | — |
| 26 | `current_y_deviation_pct` | Derived | `float64` | % | — | 1 | `(current_y - current_avg) / current_avg * 100` | 11 | Derived parameter |
| 27 | `frequency_hz` | Measured | `float` | Hz | `40157` | 1 | — | — | — |
| 28 | `kpi_displacement_pf` | Derived | `float64` | PF | — | 1 | `power_factor_total` | 12 | Derived parameter |
| 29 | `kpi_neutral_to_phase_ratio_pct` | Derived | `float64` | % | — | 1 | `current_neutral / current_avg * 100` | 13 | Derived parameter |
| 30 | `kpi_true_pf` | Derived | `float64` | PF | — | 1 | `abs(active_power_total_kw) / apparent_power_total_kva` | 14 | Derived parameter |
| 31 | `kpi_voltage_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_avg_vln - 240) / 240 * 100` | 15 | Derived parameter |
| 32 | `neutral_stress_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(kpi_neutral_to_phase_ratio_pct - 20))` | 16 | Derived parameter |
| 33 | `pf_gap_vs_full_load` | Derived | `float64` | PF | — | 1 | `kpi_displacement_pf - kpi_true_pf` | 17 | Derived parameter |
| 34 | `phase_angle_deg` | Derived | `float64` | ° | — | 1 | `acos(max(-1, min(1, power_factor_total))) * 180 / pi` | 18 | Derived parameter |
| 35 | `power_factor_b` | Measured | `float` | PF | `40123` | 0.01 | — | — | — |
| 36 | `power_factor_r` | Measured | `float` | PF | `40119` | 0.01 | — | — | — |
| 37 | `power_factor_total` | Measured | `float` | PF | `40117` | 0.1 | — | — | — |
| 38 | `power_factor_y` | Measured | `float` | PF | `40121` | 0.01 | — | — | — |
| 39 | `sag_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(-10 - kpi_voltage_deviation_pct))` | 19 | Derived parameter |
| 40 | `swell_event_active` | Derived | `float64` | 0/1 | — | 1 | `max(0, signum(kpi_voltage_deviation_pct - 10))` | 20 | Derived parameter |
| 41 | `voltage_avg_vll` | Measured | `float` | Volt | `40133` | 1 | — | — | — |
| 42 | `voltage_avg_vln` | Measured | `float` | Volt | `40141` | 1 | — | — | — |
| 43 | `voltage_b_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_b_n - 240) / 240 * 100` | 21 | Derived parameter |
| 44 | `voltage_b_n` | Measured | `float` | Volt | `40147` | 1 | — | — | — |
| 45 | `voltage_br` | Measured | `float` | Volt | `40139` | 1 | — | — | — |
| 46 | `voltage_ll_unbalance_pct` | Derived | `float64` | % | — | 1 | `max(abs(voltage_ry - voltage_avg_vll), abs(voltage_yb - voltage_avg_vll), abs(voltage_br - voltage_avg_vll)) / voltage_avg_vll * 100` | 22 | Derived parameter |
| 47 | `voltage_max` | Derived | `float64` | V | — | 1 | `max(voltage_r_n, voltage_y_n, voltage_b_n)` | 23 | Derived parameter |
| 48 | `voltage_min` | Derived | `float64` | V | — | 1 | `min(voltage_r_n, voltage_y_n, voltage_b_n)` | 24 | Derived parameter |
| 49 | `voltage_r_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_r_n - 240) / 240 * 100` | 25 | Derived parameter |
| 50 | `voltage_r_n` | Measured | `float` | Volt | `40143` | 1 | — | — | — |
| 51 | `voltage_ry` | Measured | `float` | Volt | `40135` | 1 | — | — | — |
| 52 | `voltage_unbalance_pct` | Derived | `float64` | % | — | 1 | `max(abs(voltage_r_n - voltage_avg_vln), abs(voltage_y_n - voltage_avg_vln), abs(voltage_b_n - voltage_avg_vln)) / voltage_avg_vln * 100` | 26 | Derived parameter |
| 53 | `voltage_yb` | Measured | `float` | Volt | `40137` | 1 | — | — | — |
| 54 | `voltage_y_deviation_pct` | Derived | `float64` | % | — | 1 | `(voltage_y_n - 240) / 240 * 100` | 27 | Derived parameter |
| 55 | `voltage_y_n` | Measured | `float` | Volt | `40145` | 1 | — | — | — |
