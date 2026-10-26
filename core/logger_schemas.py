"""Import LoggerFast schema blueprints and MFM assignments from audited files."""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction

from core.models import LoggerSchema, LoggerSchemaField, MFM


_SCHEMA_HEADING = re.compile(r'^### `([^`]+)`$')
_INTERNAL_ID = re.compile(r'^Internal ID: `([^`]+)`')
_FIELD_ROW = re.compile(r'^\|\s*(\d+)\s*\|')


@dataclass(frozen=True)
class FieldSpec:
    position: int
    name: str
    kind: str
    data_type: str
    unit: str
    address: str
    scale: float | None
    formula: str
    evaluation_order: int | None
    description: str


@dataclass(frozen=True)
class SchemaSpec:
    code: str
    logger_id: str
    fields: tuple[FieldSpec, ...]


def _cell(value: str) -> str:
    value = value.strip()
    if value == '—':
        return ''
    if len(value) >= 2 and value[0] == value[-1] == '`':
        return value[1:-1]
    return value


def parse_schema_details(text: str) -> tuple[SchemaSpec, ...]:
    """Parse the audited Markdown export in ``mfm_schema_details.md``.

    The parser deliberately accepts only the documented table shape. A partial
    or malformed export fails before any database write occurs.
    """
    found = []
    code = logger_id = None
    fields: list[FieldSpec] = []

    def finish():
        nonlocal code, logger_id, fields
        if code is None:
            return
        if not logger_id:
            raise ValueError(f'{code}: missing Internal ID')
        if not fields:
            raise ValueError(f'{code}: no field definitions')
        positions = [field.position for field in fields]
        if positions != list(range(1, len(fields) + 1)):
            raise ValueError(f'{code}: field positions are incomplete or out of order')
        found.append(SchemaSpec(code, logger_id, tuple(fields)))
        code = logger_id = None
        fields = []

    for raw in text.splitlines():
        heading = _SCHEMA_HEADING.match(raw)
        if heading:
            finish()
            code = heading.group(1)
            continue
        if code is None:
            continue
        identity = _INTERNAL_ID.match(raw)
        if identity:
            logger_id = identity.group(1)
            continue
        if not _FIELD_ROW.match(raw):
            continue
        cells = [_cell(cell) for cell in raw.strip().strip('|').split('|')]
        if len(cells) != 10:
            raise ValueError(f'{code}: expected 10 cells in field row: {raw}')
        position, name, kind, data_type, unit, address, scale, formula, order, description = cells
        kind = kind.casefold()
        if kind not in {'measured', 'derived'}:
            raise ValueError(f'{code}/{name}: unknown field kind {kind!r}')
        try:
            scale_value = float(scale) if scale else None
            order_value = int(order) if order else None
        except ValueError as exc:
            raise ValueError(f'{code}/{name}: invalid scale or evaluation order') from exc
        fields.append(FieldSpec(
            position=int(position), name=name, kind=kind, data_type=data_type,
            unit=unit, address=address, scale=scale_value, formula=formula,
            evaluation_order=order_value, description=description,
        ))
    finish()
    if not found:
        raise ValueError('No LoggerFast schema definitions were found')
    codes = [schema.code for schema in found]
    ids = [schema.logger_id for schema in found]
    if len(codes) != len(set(codes)) or len(ids) != len(set(ids)):
        raise ValueError('Schema codes and internal IDs must be unique')
    return tuple(found)


def schema_assignment_rows(
        workbook, categories=('HT', 'AMF', 'PCC', 'UPS', 'UTILITY', 'PRODUCTION')):
    """Return ``(sheet_row, display_name, schema_code)`` assignments.

    In this audited workbook ``Table name`` supplied the human-facing MFM name.
    The physical LoggerFast ``gic_*`` table is already stored on ``MFM`` and is
    intentionally not replaced here.
    """
    try:
        sheet = workbook['Meters']
    except KeyError:
        raise ValueError('Workbook has no Meters sheet') from None
    rows = sheet.iter_rows(values_only=True)
    try:
        header = [str(value or '').strip() for value in next(rows)]
    except StopIteration:
        raise ValueError('Meters sheet is empty') from None
    required = ('Nav category', 'Table name', 'schema')
    missing = [name for name in required if name not in header]
    if missing:
        raise ValueError(f'Meters sheet is missing: {", ".join(missing)}')
    index = {name: header.index(name) for name in required}
    wanted = set(categories)
    assignments = []
    for number, row in enumerate(rows, start=2):
        category = str(row[index['Nav category']] or '').strip()
        if category not in wanted:
            continue
        table = str(row[index['Table name']] or '').strip()
        code = str(row[index['schema']] or '').strip()
        if not table or not code:
            raise ValueError(f'Meters row {number}: Table name and schema are required')
        assignments.append((number, table, code))
    return tuple(assignments)


@transaction.atomic
def install_schema_catalog(specs, assignments, *, source='mfm_schema_details.md'):
    """Idempotently install verified blueprints and assign existing MFMs."""
    schema_by_code = {}
    created_schemas = created_fields = assigned = unchanged = 0
    for spec in specs:
        # Verified on the physical gic_* tables linked by the audited workbook.
        timestamp_column = 'timestamp_utc'
        schema, created = LoggerSchema.objects.get_or_create(
            code=spec.code, version=1,
            defaults={
                'name': spec.code,
                'logger_id': spec.logger_id,
                'timestamp_column': timestamp_column,
                'description': f'Imported from {source}.',
            },
        )
        if created:
            created_schemas += 1
        elif schema.logger_id != spec.logger_id:
            raise ValueError(
                f'{spec.code}_v1 already exists with LoggerFast id '
                f'{schema.logger_id!r}, not {spec.logger_id!r}')
        elif schema.timestamp_column != timestamp_column:
            raise ValueError(
                f'{spec.code}_v1 already uses timestamp column '
                f'{schema.timestamp_column!r}, not {timestamp_column!r}')
        schema_by_code[spec.code] = schema
        existing = {field.name: field for field in schema.fields.all()}
        incoming_names = {field.name for field in spec.fields}
        extra = sorted(set(existing) - incoming_names)
        if extra:
            raise ValueError(
                f'{spec.code}_v1 is immutable and has fields absent from the source: '
                f'{", ".join(extra)}')
        for field in spec.fields:
            values = {
                'kind': field.kind,
                'data_type': field.data_type,
                'unit': field.unit,
                'address': field.address,
                'scale': field.scale,
                'formula': field.formula,
                'evaluation_order': field.evaluation_order,
                'position': field.position,
                'description': field.description,
            }
            current = existing.get(field.name)
            if current is None:
                LoggerSchemaField.objects.create(
                    logger_schema=schema, name=field.name, **values)
                created_fields += 1
                continue
            differences = [key for key, value in values.items()
                           if getattr(current, key) != value]
            if differences:
                raise ValueError(
                    f'{spec.code}_v1/{field.name} is immutable and differs in '
                    f'{", ".join(differences)}; create a new schema version')

    known_codes = set(schema_by_code)
    used_codes = {code for _row, _table, code in assignments}
    unknown = sorted(used_codes - known_codes)
    if unknown:
        raise ValueError(f'Workbook refers to unknown schemas: {", ".join(unknown)}')

    sheet_rows = [row for row, _name, _code in assignments]
    mfms = {mfm.sheet_row: mfm for mfm in MFM.objects.filter(sheet_row__in=sheet_rows)}
    missing = [(row, name) for row, name, _code in assignments if row not in mfms]
    if missing:
        detail = ', '.join(f'row {row}: {name}' for row, name in missing)
        raise ValueError(f'No existing MFM matches workbook row(s): {detail}')
    mismatched = [(row, name, mfms[row].name) for row, name, _code in assignments
                  if mfms[row].name != name]
    if mismatched:
        detail = ', '.join(
            f'row {row}: sheet={sheet!r}, database={database!r}'
            for row, sheet, database in mismatched)
        raise ValueError(f'MFM sheet-row identity does not match: {detail}')
    for row, _name, code in assignments:
        mfm = mfms[row]
        schema = schema_by_code[code]
        if mfm.logger_schema_id == schema.id:
            unchanged += 1
            continue
        mfm.logger_schema = schema
        mfm.save(update_fields=['logger_schema', 'updated_at'])
        assigned += 1
    return {
        'schemas_created': created_schemas,
        'fields_created': created_fields,
        'mfms_assigned': assigned,
        'mfms_unchanged': unchanged,
    }
