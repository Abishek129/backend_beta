"""Plant-specific import of the audited MFM workbook.

Unlike the public upload template, the supplied audit sheet contains human
table labels and engineering values with units.  This adapter preserves that
source while applying the confirmed hierarchy corrections and resolving each
label to its LoggerFast device/table binding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from core import logger_devices
from core.equipment import (ensure_mfm_equipment, ensure_panel_equipment,
                            ensure_primary_reference)
from core.models import Group, LoggerSchema, Membership, MFM, MfmConfig, PanelConfig
from core.nav_categories import panel_axes
from core.services import reset_energy_directions
from core.slugs import free_slug

AUDITED_CATEGORIES = ('HT', 'AMF', 'PCC', 'UPS', 'UTILITY', 'PRODUCTION')

# Rows whose source label differs from LoggerFast, plus the duplicated
# AIR_COMPRESSOR_PDB label. Logger device ids are the durable identities.
DEVICE_ID_BY_ROW = {
    20: 'dev_1789541712583', 41: 'dev_1788766435335',
    49: 'dev_1788766435382', 50: 'dev_1788766435383',
    51: 'dev_1788766435384', 52: 'dev_1788766435385',
    78: 'dev_1788766435411', 83: 'dev_1788766435416',
}


@dataclass(frozen=True)
class AuditedMeter:
    row: int
    name: str
    category: str
    panel: str
    role: str
    source_label: str
    schema_code: str
    nominal_voltage_v: float | None
    rated_kva: float | None
    rated_kw: float | None
    rated_current_a: float | None
    frequency_hz: float | None
    contracted_kw: float | None


def _canon(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').casefold())


def _engineering(value, unit, *, scale=1.0):
    if value in (None, ''):
        return None
    text = str(value).strip().replace(',', '').upper()
    match = re.fullmatch(r'([-+]?[0-9]*\.?[0-9]+)\s*([A-Z]+)?', text)
    if not match:
        raise ValueError(f'invalid engineering value {value!r}')
    number, suffix = float(match.group(1)), match.group(2) or unit
    # Audited row 6 says ``4000V`` in the *Rated current (A)* column. The column
    # is authoritative here; retain the intended 4000 A rather than dropping an
    # otherwise valid AMF incomer because of the suffix typo.
    if unit == 'A' and suffix == 'V':
        suffix = 'A'
    multipliers = {
        ('V', 'V'): 1, ('V', 'KV'): 1000,
        ('KVA', 'KVA'): 1, ('KVA', 'MVA'): 1000,
        ('KW', 'KW'): 1, ('KW', 'MW'): 1000,
        ('A', 'A'): 1, ('HZ', 'HZ'): 1, ('KA', 'KA'): 1,
    }
    try:
        return number * multipliers[(unit, suffix)] * scale
    except KeyError:
        raise ValueError(f'expected {unit}, got {value!r}') from None


def audited_meter_rows(workbook):
    """Parse rows and apply the confirmed panel semantics.

    HT rows 2-5 are one 33KV panel; UPS and Utility are one panel each;
    Production meters are loose category leaves with no role.
    """
    sheet = workbook['Meters']
    rows = sheet.iter_rows(values_only=True)
    header = [str(value or '').strip().casefold() for value in next(rows)]

    def col(name):
        try:
            return header.index(name.casefold())
        except ValueError:
            raise ValueError(f'Meters sheet is missing {name!r}') from None

    indexes = {name: col(name) for name in (
        'Nav category', 'Distribution panel', 'Role', 'Table name',
        'Nominal voltage (V)', 'Rated kVA', 'Rated kW', 'Rated current (A)',
        'Frequency (Hz)', 'Contracted kW', 'schema')}
    parsed = []
    for number, values in enumerate(rows, start=2):
        category = str(values[indexes['Nav category']] or '').strip().upper()
        if category not in AUDITED_CATEGORIES:
            continue
        source = str(values[indexes['Table name']] or '').strip()
        if not source:
            raise ValueError(f'Meters row {number}: Table name is required')
        panel = str(values[indexes['Distribution panel']] or '').strip()
        role = str(values[indexes['Role']] or '').strip().casefold()
        role = {'incomer': 'incoming', 'incoming': 'incoming',
                'outgoer': 'outgoing', 'outgoing': 'outgoing',
                'spare': 'spare'}.get(role, role)
        if category == 'HT' and 2 <= number <= 5:
            panel = '33KV HT Panel'
        elif category == 'UPS':
            panel = 'UPS'
        elif category == 'UTILITY':
            panel = 'Utility Panel'
        elif category == 'PRODUCTION':
            panel, role = '', ''
        if panel and role not in {'incoming', 'outgoing', 'spare'}:
            raise ValueError(f'Meters row {number}: panel membership requires a valid role')
        parsed.append(AuditedMeter(
            row=number, name=source, category=category.casefold(), panel=panel,
            role=role, source_label=source,
            schema_code=str(values[indexes['schema']] or '').strip(),
            nominal_voltage_v=_engineering(values[indexes['Nominal voltage (V)']], 'V'),
            rated_kva=_engineering(values[indexes['Rated kVA']], 'KVA'),
            rated_kw=_engineering(values[indexes['Rated kW']], 'KW'),
            rated_current_a=_engineering(values[indexes['Rated current (A)']], 'A'),
            frequency_hz=_engineering(values[indexes['Frequency (Hz)']], 'HZ'),
            contracted_kw=_engineering(values[indexes['Contracted kW']], 'KW'),
        ))
    return tuple(parsed)


def _device_for(row, devices, existing):
    explicit = DEVICE_ID_BY_ROW.get(row.row)
    if explicit:
        device = devices.get(explicit)
        if device is None:
            raise ValueError(f'Meters row {row.row}: LoggerFast device {explicit} is missing')
        return device
    current = existing.get(row.row)
    if current and current.logger_id in devices:
        return devices[current.logger_id]
    matches = [device for device in devices.values()
               if device.get('table_name') and _canon(device.get('name')) == _canon(row.source_label)]
    if len(matches) != 1:
        raise ValueError(
            f'Meters row {row.row}: expected one LoggerFast match for {row.source_label!r}, '
            f'found {len(matches)}')
    return matches[0]


@transaction.atomic
def install_audited_meters(rows, devices, *, commit=False):
    """Idempotently create/update audited panels, MFMs, roles and bindings."""
    schemas = {schema.code: schema for schema in LoggerSchema.objects.filter(active=True)}
    unknown = sorted({row.schema_code for row in rows if row.schema_code not in schemas})
    if unknown:
        raise ValueError(f'Unknown logger schemas: {", ".join(unknown)}')
    existing = {mfm.sheet_row: mfm for mfm in MFM.objects.filter(
        sheet_row__in=[row.row for row in rows]).select_related('config')}
    panels = {}
    for row in rows:
        if not row.panel or row.panel.casefold() in panels:
            continue
        group = Group.objects.filter(active=True, name__iexact=row.panel).first()
        if group is not None and not group.is_panel:
            raise ValueError(f'{row.panel!r} exists but is not a distribution panel')
        kind, nav = panel_axes(row.category)
        if group is None:
            group = Group.objects.create(
                name=row.panel, slug=free_slug(row.panel), is_panel=True,
                nav_category=nav, nav_category_source='manual')
        else:
            group.nav_category, group.nav_category_source = nav, 'manual'
            group.save(update_fields=['nav_category', 'nav_category_source'])
        config, _ = PanelConfig.objects.get_or_create(group=group)
        if config.panel_kind != kind:
            config.panel_kind = kind
            config.save(update_fields=['panel_kind'])
        ensure_panel_equipment(group)
        panels[row.panel.casefold()] = group

    counters, created, updated, memberships = {}, 0, 0, 0
    production_ids = []
    for row in rows:
        device = _device_for(row, devices, existing)
        if not device.get('table_name'):
            raise ValueError(f'Meters row {row.row}: LoggerFast device has no migrated table')
        mfm = (MFM.objects.filter(logger_id=device['id']).first()
               or existing.get(row.row))
        if mfm is None:
            mfm = MFM(name=row.name, slug=free_slug(row.name))
            created += 1
        else:
            updated += 1
        data_source = logger_devices.data_source_for(device.get('db_target_id', ''))
        values = {
            'name': row.name, 'logger_id': device['id'], 'protocol': device.get('protocol', ''),
            'gateway_id': device.get('gateway_id', ''), 'unit_id': device.get('unit_id'),
            'port': device.get('port'), 'logger_status': device.get('status', ''),
            'data_source': data_source, 'logger_schema': schemas[row.schema_code],
            'table_name': device['table_name'], 'logger_table_id': device.get('table_id', ''),
            'table_binding': 'manual', 'sheet_row': row.row, 'active': True,
            'retired_at': None, 'rated_capacity_kva': row.rated_kva,
            'nominal_voltage_v': row.nominal_voltage_v, 'frequency_hz': row.frequency_hz,
            'nav_category': row.category if not row.panel else '',
            'nav_category_source': 'manual',
        }
        for field, value in values.items():
            setattr(mfm, field, value)
        mfm.save()
        config, _ = MfmConfig.objects.get_or_create(mfm=mfm)
        config.device_type = 'lt_panel'
        for field in ('rated_kva', 'rated_kw', 'rated_current_a', 'frequency_hz',
                      'contracted_kw'):
            setattr(config, field, getattr(row, field))
        config.save()
        ensure_mfm_equipment(mfm)
        if row.panel:
            group = panels[row.panel.casefold()]
            key = (group.id, row.role)
            position = counters[key] = counters.get(key, -1) + 1
            Membership.objects.update_or_create(
                group=group, mfm=mfm,
                defaults={'role': row.role, 'position': position})
            ensure_primary_reference(mfm)
            memberships += 1
        else:
            production_ids.append(mfm.id)

    if production_ids:
        Membership.objects.filter(mfm_id__in=production_ids, group__is_panel=True).delete()
        MFM.objects.filter(id__in=production_ids).update(reference=None)
    retired = 0
    for group in Group.objects.filter(active=True, is_panel=True, nav_category='production'):
        group.active, group.retired_at = False, timezone.now()
        group.save(update_fields=['active', 'retired_at'])
        retired += 1
    for group in panels.values():
        equipment = ensure_panel_equipment(group)
        if not equipment.metered:
            equipment.metered = True
            equipment.save(update_fields=['metered', 'updated_at'])
    if not commit:
        transaction.set_rollback(True)
    else:
        reset_energy_directions()
    return {'rows': len(rows), 'created': created, 'updated': updated,
            'memberships': memberships, 'production_panels_retired': retired,
            'committed': commit}
