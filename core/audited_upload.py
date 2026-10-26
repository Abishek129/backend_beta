"""Normalize the plant workbook for the transactional sheet importer."""
from __future__ import annotations

import re

from core.audited_workbook import _engineering
from core import logger_devices
from core.logger_client import LoggerApiError
from core.models import MFM
from core.sheet_import import (METERS, PANELS, METER_COLUMNS, Report,
                               read_upload, run_import)

_MAX_ROWS = 5000
_UNITS = {
    'nominal voltage (v)': 'V', 'rated kva': 'KVA', 'rated kw': 'KW',
    'rated current (a)': 'A', 'frequency (hz)': 'HZ', 'contracted kw': 'KW',
}
_PANEL_UNITS = {
    **_UNITS, 'fault level (ka)': 'KA', 'critical load (kw)': 'KW',
}
_METER_HEADER = [column.label for column in METER_COLUMNS]
_CANON = lambda value: re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())
_UPS_ALIASES = {f'UPS-{letter}': f'I/C_UPS_{letter}' for letter in 'ABCD'}
_KNOWN_DEVICE_IDS = {
    ('OG_33_415kV_TF_1', '33KV HT Panel'): 'dev_1788520308453',
    ('OG_33_415kV_TF_2', '33KV HT Panel'): 'dev_1788520308457',
    ('OG_33_415kV_TF_3', '33KV HT Panel'): 'dev_1788520308460',
    ('AIR_COMPRESSOR_PDB', 'PCC-1'): 'dev_1789541712583',
    ('AIR_COMPRESSOR_PDB', 'PCC-3'): 'dev_1788766435335',
    ('O/G_EXHAUST_UNIT_PDB_02', 'Utility Panel'): 'dev_1788766435411',
    ('O/G_PRE_LAMINATOR_PDB_AND_LDB', 'Utility Panel'): 'dev_1788766435416',
}
_HT_LEGACY = {
    'OG_33_415kV_TF_1': '34KV HT Panel',
    'OG_33_415kV_TF_2': '35KV HT Panel',
    'OG_33_415kV_TF_3': '36KV HT Panel',
}


def _headers(header):
    return {_CANON(value): index for index, value in enumerate(header) if value is not None}


def _cell(values, mapping, label):
    index = mapping.get(_CANON(label))
    return values[index] if index is not None and index < len(values) else None


def _text(value):
    return str(value or '').strip()


def _lookup_device(label, panel, explicit_id, physical_table, catalog, existing_row):
    if explicit_id:
        candidate = catalog.get(explicit_id)
        matches = [candidate] if candidate else []
    elif physical_table:
        matches = [d for d in catalog.values() if d['table_name'] == physical_table]
    else:
        known_id = _KNOWN_DEVICE_IDS.get((label, panel))
        if known_id:
            matches = [catalog[known_id]] if known_id in catalog else []
        else:
            matches = [d for d in catalog.values() if d.get('table_name') and
                       (d['table_name'] == label or _CANON(d['name']) == _CANON(label))]
        if not matches and label in _UPS_ALIASES:
            matches = [d for d in catalog.values()
                       if d.get('table_name') and
                       _CANON(d['name']) == _CANON(_UPS_ALIASES[label])]
        if len(matches) != 1 and existing_row and existing_row.name == label:
            matches = [d for d in matches if d['id'] == existing_row.logger_id]
            if not matches and existing_row.logger_id in catalog:
                matches = [catalog[existing_row.logger_id]]
    if len(matches) != 1 or not matches[0].get('table_name'):
        raise ValueError(
            f'{label!r} does not resolve to one migrated LoggerFast table; '
            'provide Logger device ID or Physical table for ambiguous names')
    device = matches[0]
    if physical_table and physical_table != device['table_name']:
        raise ValueError(f'Physical table {physical_table!r} does not match the selected device')
    return device


def prepare_audited_upload(sheets, catalog):
    """Return normalized generic sheets and a validation report, without writes."""
    report = Report()
    header, source_rows = sheets[METERS]
    mapping = _headers(header)
    for required in ('Nav category', 'Table name'):
        if _CANON(required) not in mapping:
            report.error(METERS, 1, f'Missing column {required!r}.')
    if not source_rows:
        report.error(METERS, 1, 'The Meters sheet has no data rows.')
    if len(source_rows) > _MAX_ROWS:
        report.error(METERS, 1, f'More than {_MAX_ROWS} rows are not supported.')
    if not report.ok:
        return {}, report

    sheet_rows = [number for number, _values in source_rows]
    existing = {m.sheet_row: m for m in MFM.objects.filter(sheet_row__in=sheet_rows)}
    transformed = []
    for number, values in source_rows:
        category = _text(_cell(values, mapping, 'Nav category'))
        label = _text(_cell(values, mapping, 'Table name'))
        panel = _text(_cell(values, mapping, 'Distribution panel'))
        role = _text(_cell(values, mapping, 'Role'))
        if not category or not label:
            report.error(METERS, number, 'Nav category and Table name are required.')
            continue
        category_code = category.casefold()
        if category_code == 'ups' and not panel:
            panel = 'UPS'
        elif category_code == 'utility' and not panel:
            panel = 'Utility Panel'
        elif category_code == 'production':
            if panel or role:
                report.error(METERS, number,
                             'Production MFMs must be standalone: leave panel and role blank.')
                continue
        if category_code == 'ht' and _HT_LEGACY.get(label) == panel:
            panel = '33KV HT Panel'
            report.warn(METERS, number, 'Corrected the known 33KV HT panel name typo.')
        if not panel and role:
            report.error(METERS, number, 'Role requires a Distribution panel.')
            continue
        if panel and not role:
            report.error(METERS, number, 'A Distribution panel requires a Role.')
            continue
        explicit_id = _text(_cell(values, mapping, 'Logger device ID'))
        physical_table = _text(_cell(values, mapping, 'Physical table'))
        try:
            device = _lookup_device(label, panel, explicit_id, physical_table,
                                    catalog, existing.get(number))
        except ValueError as exc:
            report.error(METERS, number, str(exc))
            continue

        normalized = {
            # The supplied workbook's MFM-1/MFM-2 values repeat per panel;
            # the table/device label is the actual stable display name.
            'name': label if label != device['table_name'] else device['name'],
            'category': category, 'panel': panel, 'role': role,
            'table_name': device['table_name'],
            'logger_schema': _text(_cell(values, mapping, 'Schema')),
        }
        for label_name, unit in _UNITS.items():
            raw = _cell(values, mapping, label_name)
            if raw in (None, ''):
                continue
            try:
                normalized[{
                    'nominal voltage (v)': 'nominal_voltage_v',
                    'rated kva': 'rated_kva', 'rated kw': 'rated_kw',
                    'rated current (a)': 'rated_current_a',
                    'frequency (hz)': 'frequency_hz', 'contracted kw': 'contracted_kw',
                }[label_name]] = _engineering(raw, unit)
            except ValueError as exc:
                report.error(METERS, number, f'{label_name}: {exc}')
        transformed.append((number, [normalized.get(column.key) for column in METER_COLUMNS]))

    if not report.ok:
        return {}, report
    normalized_sheets = {METERS: (_METER_HEADER, transformed)}
    if PANELS in sheets:
        panel_header, panel_rows = sheets[PANELS]
        panel_mapping = _headers(panel_header)
        converted_panels = []
        for number, values in panel_rows:
            converted = list(values)
            for label_name, unit in _PANEL_UNITS.items():
                index = panel_mapping.get(_CANON(label_name))
                if index is None or index >= len(converted) or converted[index] in (None, ''):
                    continue
                try:
                    converted[index] = _engineering(converted[index], unit)
                except ValueError as exc:
                    report.error(PANELS, number, f'{label_name}: {exc}')
            converted_panels.append((number, converted))
        if not report.ok:
            return {}, report
        normalized_sheets[PANELS] = (panel_header, converted_panels)
    return normalized_sheets, report


def run_audited_upload(filename, data, *, commit=False):
    """Validate/preview or atomically commit a workbook like the shared sheet."""
    sheets = read_upload(filename, data)
    try:
        catalog = logger_devices.devices(strict=True)
    except LoggerApiError as exc:
        report = Report()
        report.error(METERS, None, f'LoggerFast is unavailable: {exc}')
        return report
    normalized, preparation = prepare_audited_upload(sheets, catalog)
    if not preparation.ok:
        return preparation
    result = run_import(normalized, commit=commit)
    result.warnings[:0] = preparation.warnings
    return result
