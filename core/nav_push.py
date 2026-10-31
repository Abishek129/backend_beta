"""Push the whole EMS sidebar in one request — ``POST /api/ems/push/``.

The body is the nav exactly as the sidebar should read: categories in order,
each with its distribution panels (members by role) and its standalone meters,
plus the logger schemas the meters' tables are read with::

    {"schemas": [<definition>, …],                         optional
     "categories": [                                      required, in order
       {"code": "ht", "label": "HT Panels", "slug": "ht-panels",
        "panel_kind": "ht_panel", "hide_when_empty": false,
        "panels": [{"mfm_id": 2, "name": "33KV HT Panel", "panel_kind": "ht_panel",
                    "nameplate": {"nominal_voltage_v": 33000},
                    "incoming": [<meter>], "outgoing": [<meter>], "spare": [<meter>]}],
        "meters": [<meter>]}]}                            standalone meters

    <meter>      {"mfm_id": 1000005} for an existing meter, or {"table_name": …}
                 (a LoggerFast table) to find-or-create one; optional "name",
                 "table_name" (rebinds), "schema", "nameplate"
    "schema"     "sch_X" (version 1), {"code", "version"}, or a full <definition>
    <definition> {"code", "version", "name", "logger_id", "timestamp_column",
                  "description", "fields": [{"name", "kind", "data_type", "unit",
                  "address", "scale", "formula", "evaluation_order", "description"}]}

It is a FULL SYNC — what the push leaves out leaves the sidebar:

    categories   pushed ones are upserted in push order; the rest are hidden
                 (``active=False``, never deleted)
    panels       pushed ones are created/updated/reactivated; every other active
                 panel is soft-retired (id, config and memberships kept)
    memberships  a pushed panel's members become exactly the pushed lists
    meters       never retired. One the push does not file standalone is unfiled
                 (``nav_category=''``, pinned); an active plain group that would
                 still show an unlisted meter is soft-retired
    schemas      definitions are installed; a stored version is immutable

A preview by default: the same code runs in a transaction that is rolled back,
so the report — and the ``nav`` it returns — is exactly what a commit would
produce. ``commit`` writes only when there are no errors at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from core import logger_devices
from core.constants import FEEDER_ID_OFFSET
from core.equipment import (ensure_mfm_equipment, ensure_panel_equipment,
                            ensure_primary_reference)
from core.logger_client import LoggerApiError
from core.logger_schemas import FieldSpec, SchemaSpec, install_schema
from core.models import (PANEL_KIND_CHOICES, Group, LoggerSchema, Membership, MFM,
                         MfmConfig, NameplateBase, NavCategory, PanelConfig)
from core.nav import Categories, build_tree, resolved_category
from core.sheet_import import bind_device
from core.slugs import free_slug, slug_owner, slugify

ROLES = ('incoming', 'outgoing', 'spare')
_TOP_KEYS = {'schemas', 'categories'}
_CATEGORY_KEYS = {'code', 'label', 'slug', 'panel_kind', 'hide_when_empty', 'panels', 'meters'}
_PANEL_KEYS = {'mfm_id', 'name', 'panel_kind', 'nameplate', *ROLES}
_METER_KEYS = {'mfm_id', 'name', 'table_name', 'schema', 'nameplate'}
_SCHEMA_KEYS = {'code', 'version', 'name', 'logger_id', 'timestamp_column', 'description',
                'fields'}
_FIELD_KEYS = {'name', 'kind', 'data_type', 'unit', 'address', 'scale', 'formula',
               'evaluation_order', 'description'}
_KINDS = frozenset(kind for kind, _label in PANEL_KIND_CHOICES)
_NAMEPLATE = frozenset(NameplateBase.CONFIG_FIELDS)
_CODE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,99}$')
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
@dataclass
class Report:
    committed: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    nav: dict | None = None

    @property
    def ok(self):
        return not self.errors

    def error(self, path, message):
        self.errors.append({'path': path, 'message': message})

    def warn(self, path, message):
        self.warnings.append({'path': path, 'message': message})

    def change(self, entity, action, name, path='', detail=''):
        counts = self.summary.setdefault(entity, {})
        counts[action] = counts.get(action, 0) + 1
        if action != 'unchanged':
            self.changes.append({'entity': entity, 'action': action, 'name': name,
                                 'path': path, 'detail': detail})

    def as_dict(self):
        out = {'ok': self.ok, 'committed': self.committed, 'summary': self.summary,
               'errors': self.errors, 'warnings': self.warnings, 'changes': self.changes}
        if self.nav is not None:
            out['nav'] = self.nav
        return out


# --------------------------------------------------------------------------
# parse + validate the JSON (no DB)
# --------------------------------------------------------------------------
@dataclass
class SchemaDef:
    path: str
    spec: SchemaSpec
    version: int
    name: str
    timestamp_column: str
    description: str

    @property
    def key(self):
        return self.spec.code, self.version

    def same_as(self, other):
        return (self.spec, self.name, self.timestamp_column, self.description) == \
               (other.spec, other.name, other.timestamp_column, other.description)


@dataclass
class MeterIn:
    path: str
    mfm_id: int | None
    name: str
    table_name: str
    schema: tuple | None             # (code, version)
    nameplate: dict
    plan: _Meter | None = None


@dataclass
class PanelIn:
    path: str
    mfm_id: int | None
    name: str
    panel_kind: str | None
    nameplate: dict
    members: list                    # [(role, MeterIn)]
    category: CategoryIn | None = None
    group: Group | None = None


@dataclass
class CategoryIn:
    path: str
    code: str
    label: str
    slug: str
    panel_kind: str | None
    hide_when_empty: bool | None
    panels: list
    meters: list
    row: NavCategory | None = None


class _Reader:
    """Typed access to one JSON object; every problem lands in the report."""

    def __init__(self, raw, path, keys, report):
        self.path, self.report = path, report
        if not isinstance(raw, dict):
            report.error(path, 'must be an object')
            raw = {}
        unknown = sorted(set(raw) - keys)
        if unknown:
            report.error(path, f'unknown key(s) {", ".join(unknown)}; '
                               f'allowed: {", ".join(sorted(keys))}')
        self.raw = raw

    def text(self, key, required=False):
        value = self.raw.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required:
                self.report.error(self.path, f'"{key}" is required')
            return ''
        if not isinstance(value, str):
            self.report.error(self.path, f'"{key}" must be a string')
            return ''
        # Ends only: a schema field's text must compare equal to the stored one.
        return value.strip()

    def whole(self, key, default=None, minimum=None):
        value = self.raw.get(key)
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) \
                or (minimum is not None and value < minimum):
            bound = f' ≥ {minimum}' if minimum is not None else ''
            self.report.error(self.path, f'"{key}" must be a whole number{bound}')
            return default
        return value

    def number(self, key):
        value = self.raw.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.report.error(self.path, f'"{key}" must be a number')
            return None
        return float(value)

    def flag(self, key):
        value = self.raw.get(key)
        if value is not None and not isinstance(value, bool):
            self.report.error(self.path, f'"{key}" must be true or false')
            return None
        return value

    def items(self, key, required=False):
        value = self.raw.get(key)
        if value is None:
            if required:
                self.report.error(self.path, f'"{key}" is required')
            return []
        if not isinstance(value, list):
            self.report.error(self.path, f'"{key}" must be a list')
            return []
        return value

    def kind(self, key):
        value = self.text(key)
        if value and value not in _KINDS:
            self.report.error(self.path, f'"{key}" must be one of {", ".join(sorted(_KINDS))}')
            return None
        return value or None

    def nameplate(self):
        raw = self.raw.get('nameplate')
        if raw is None:
            return {}
        sub = _Reader(raw, f'{self.path}.nameplate', _NAMEPLATE, self.report)
        values = {}
        for key in sub.raw:
            if key in _NAMEPLATE:
                values[key] = sub.number(key)      # null clears back to the defaults
        return values


def _schema(raw, path, report):
    r = _Reader(raw, path, _SCHEMA_KEYS, report)
    code = r.text('code', required=True)
    ts = r.text('timestamp_column', required=True)
    if ts and not _IDENTIFIER.match(ts):
        report.error(path, f'"timestamp_column" {ts!r} is not a safe column name')
    fields, names, orders = [], set(), set()
    for j, raw_field in enumerate(r.items('fields', required=True)):
        f = _Reader(raw_field, f'{path}.fields[{j}]', _FIELD_KEYS, report)
        name, kind = f.text('name', required=True), f.text('kind', required=True).casefold()
        if kind and kind not in ('measured', 'derived'):
            report.error(f.path, '"kind" must be measured or derived')
        spec = FieldSpec(
            position=j + 1, name=name, kind=kind,
            data_type=f.text('data_type', required=True), unit=f.text('unit'),
            address=f.text('address'), scale=f.number('scale'), formula=f.text('formula'),
            evaluation_order=f.whole('evaluation_order', minimum=0),
            description=f.text('description'))
        if kind == 'measured' and not spec.address:
            report.error(f.path, 'a measured field needs an "address"')
        if kind == 'derived' and not spec.formula:
            report.error(f.path, 'a derived field needs a "formula"')
        if name in names:
            report.error(f.path, f'field "{name}" is listed twice')
        if spec.evaluation_order is not None and spec.evaluation_order in orders:
            report.error(f.path, f'evaluation_order {spec.evaluation_order} is used twice')
        names.add(name)
        orders.add(spec.evaluation_order)
        fields.append(spec)
    if 'fields' in r.raw and not fields:
        report.error(path, '"fields" must list at least one field')
    return SchemaDef(path=path, spec=SchemaSpec(code, r.text('logger_id'), tuple(fields)),
                     version=r.whole('version', default=1, minimum=1),
                     name=r.text('name') or code, timestamp_column=ts,
                     description=r.text('description'))


def _schema_ref(raw, path, definitions, report):
    """A meter's "schema" -> ``(code, version)``; an inline definition is
    collected into ``definitions`` too."""
    if isinstance(raw, str):
        return (raw.strip(), 1) if raw.strip() else None
    if isinstance(raw, dict) and 'fields' in raw:
        schema = _schema(raw, path, report)
        definitions.append(schema)
        return schema.key
    r = _Reader(raw, path, {'code', 'version'}, report)
    code = r.text('code', required=True)
    return (code, r.whole('version', default=1, minimum=1)) if code else None


def _meter(raw, path, definitions, report):
    r = _Reader(raw, path, _METER_KEYS, report)
    mfm_id = r.whole('mfm_id')
    if mfm_id is not None and mfm_id < FEEDER_ID_OFFSET:
        report.error(path, f'a meter "mfm_id" is ≥ {FEEDER_ID_OFFSET} (the id /api/ems/ '
                           f'returns); {mfm_id} is a panel id')
        mfm_id = None
    table = r.text('table_name')
    if mfm_id is None and not table and 'mfm_id' not in r.raw:
        report.error(path, 'give "mfm_id" for an existing meter, or "table_name" to '
                           'find or create one by its LoggerFast table')
    schema = (_schema_ref(r.raw['schema'], f'{path}.schema', definitions, report)
              if r.raw.get('schema') is not None else None)
    return MeterIn(path=path, mfm_id=mfm_id, name=r.text('name'), table_name=table,
                   schema=schema, nameplate=r.nameplate())


def _panel(raw, path, definitions, report):
    r = _Reader(raw, path, _PANEL_KEYS, report)
    mfm_id = r.whole('mfm_id', minimum=1)
    if mfm_id is not None and mfm_id >= FEEDER_ID_OFFSET:
        report.error(path, f'a panel "mfm_id" is below {FEEDER_ID_OFFSET}; {mfm_id} is a '
                           f'meter id')
        mfm_id = None
    name = r.text('name')
    if mfm_id is None and not name and 'mfm_id' not in r.raw:
        report.error(path, 'give "mfm_id" for an existing panel, or a "name"')
    members = [(role, _meter(m, f'{path}.{role}[{i}]', definitions, report))
               for role in ROLES for i, m in enumerate(r.items(role))]
    return PanelIn(path=path, mfm_id=mfm_id, name=name, panel_kind=r.kind('panel_kind'),
                   nameplate=r.nameplate(), members=members)


def _category(raw, path, definitions, report):
    r = _Reader(raw, path, _CATEGORY_KEYS, report)
    cat = CategoryIn(
        path=path, code=r.text('code').lower(), label=r.text('label'),
        slug=r.text('slug').lower(), panel_kind=r.kind('panel_kind'),
        hide_when_empty=r.flag('hide_when_empty'), panels=[], meters=[])
    if not (cat.code or cat.label or cat.slug):
        report.error(path, 'give the category a "code" (existing) or a "label" (new)')
    for i, p in enumerate(r.items('panels')):
        panel = _panel(p, f'{path}.panels[{i}]', definitions, report)
        panel.category = cat
        cat.panels.append(panel)
    cat.meters = [_meter(m, f'{path}.meters[{i}]', definitions, report)
                  for i, m in enumerate(r.items('meters'))]
    return cat


def parse(body, report):
    """``(categories, schema definitions)`` from the request body."""
    top = _Reader(body, '', _TOP_KEYS, report)
    definitions = [_schema(s, f'schemas[{i}]', report)
                   for i, s in enumerate(top.items('schemas'))]
    raw_categories = top.items('categories', required=True)
    if 'categories' in top.raw and not raw_categories:
        report.error('categories', 'list at least one category — a push replaces the '
                                   'whole sidebar')
    categories = [_category(c, f'categories[{i}]', definitions, report)
                  for i, c in enumerate(raw_categories)]
    return categories, definitions


# --------------------------------------------------------------------------
# resolve against the DB + LoggerFast (reads only)
# --------------------------------------------------------------------------
@dataclass
class _Meter:
    """One distinct meter, however many places the push lists it."""
    path: str
    mfm: MFM | None
    name: str = ''
    table_name: str = ''
    device: dict | None = None
    schema: tuple | None = None
    nameplate: dict = field(default_factory=dict)
    standalone: CategoryIn | None = None
    panels: list = field(default_factory=list)     # [(PanelIn, path)]


def _resolve_categories(categories, report):
    rows = list(NavCategory.objects.all())
    by_code = {c.code: c for c in rows}
    by_slug = {c.slug: c for c in rows}
    by_label = {}
    for c in rows:
        by_label.setdefault(c.label.casefold(), c)
    seen_codes, seen_slugs = {}, {}
    for cat in categories:
        if cat.code:
            row = by_code.get(cat.code)
        else:
            row = by_slug.get(cat.slug) if cat.slug else by_label.get(cat.label.casefold())
        if row is None:
            if not cat.label:
                report.error(cat.path, '"label" is required for a new category')
            if cat.code and not _CODE.match(cat.code):
                report.error(cat.path, f'"code" {cat.code!r}: use lowercase letters, digits, '
                                       f'- or _ (up to 32)')
            if not cat.code:
                base, i = slugify(cat.slug or cat.label)[:32], 2
                cat.code = base
                while cat.code in by_code or cat.code in seen_codes:
                    cat.code, i = f'{base[:29]}-{i}', i + 1
            cat.slug = cat.slug or slugify(cat.label)
        else:
            cat.code, cat.slug = row.code, cat.slug or row.slug
        cat.row = row
        if cat.slug and not _SLUG.match(cat.slug):
            report.error(cat.path, f'"slug" {cat.slug!r}: use lowercase letters, digits and -')
        for seen, value, what in ((seen_codes, cat.code, 'code'), (seen_slugs, cat.slug, 'slug')):
            if value in seen:
                report.error(cat.path, f'{what} "{value}" is already used by {seen[value]}')
            seen.setdefault(value, cat.path)
        if row is None or row.slug != cat.slug:
            for model, what in ((Group, 'panel/group'), (MFM, 'MFM')):
                other = model.objects.filter(active=True, slug=cat.slug).first()
                if other is not None:
                    report.error(cat.path, f'slug "{cat.slug}" is already the {what} '
                                           f'"{other.name}"')
    pushed = {cat.row.pk for cat in categories if cat.row is not None}
    for row in rows:
        if row.pk not in pushed and row.slug in seen_slugs:
            report.error(seen_slugs[row.slug], f'slug "{row.slug}" belongs to category '
                                               f'"{row.label}", which this push hides — '
                                               f'give it another slug or push it too')


def _resolve_schemas(definitions, refs, report):
    """``{(code, version): LoggerSchema or SchemaDef}`` for every schema used."""
    defined = {}
    for d in definitions:
        seen = defined.get(d.key)
        if seen is not None and not seen.same_as(d):
            report.error(d.path, f'schema {d.key[0]} v{d.key[1]} is defined twice, '
                                 f'differently (first at {seen.path})')
        defined.setdefault(d.key, d)
        if d.spec.logger_id:
            holder = (LoggerSchema.objects.filter(logger_id=d.spec.logger_id)
                      .exclude(code=d.spec.code, version=d.version).first())
            if holder is not None:
                report.error(d.path, f'logger_id {d.spec.logger_id!r} already belongs to '
                                     f'{holder.code} v{holder.version}')
    stored = {(s.code, s.version): s for s in LoggerSchema.objects.filter(
        code__in={code for code, _v in refs})}
    for (code, version), path in refs.items():
        row = stored.get((code, version))
        if row is None and (code, version) not in defined:
            report.error(path, f'logger schema {code} v{version} does not exist — define it '
                               f'under "schemas"')
        elif row is not None and not row.active:
            report.error(path, f'logger schema {code} v{version} is retired')
    return {**stored, **defined}


def _resolve_meters(categories, report):
    appearances = [(m, cat, None, None) for cat in categories for m in cat.meters]
    appearances += [(m, cat, p, role) for cat in categories for p in cat.panels
                    for role, m in p.members]
    by_pk = MFM.objects.in_bulk({m.mfm_id - FEEDER_ID_OFFSET
                                 for m, *_ in appearances if m.mfm_id})

    # LoggerFast is only asked about tables that would change a binding, so
    # re-pushing an unchanged nav works while the logger is down.
    lookup = {}
    for m, *_ in appearances:
        current = by_pk.get(m.mfm_id - FEEDER_ID_OFFSET) if m.mfm_id else None
        if m.table_name and (current is None or current.table_name != m.table_name):
            lookup.setdefault(m.table_name, m.path)
    by_table = {}
    if lookup:
        try:
            devices = logger_devices.devices(strict=True)
        except LoggerApiError as exc:
            report.error('', f'Cannot check table names against LoggerFast: {exc}')
            return {}
        by_table = {d['table_name']: d for d in devices.values() if d['table_name']}
        for table, path in lookup.items():
            if table not in by_table:
                report.error(path, f'table "{table}" is not a migrated LoggerFast table')

    plans = {}
    for m, cat, panel, role in appearances:
        device = by_table.get(m.table_name) if m.table_name in lookup else None
        if m.mfm_id:
            row = by_pk.get(m.mfm_id - FEEDER_ID_OFFSET)
            if row is None:
                report.error(m.path, f'MFM {m.mfm_id} does not exist')
                continue
        elif device is not None:
            row = MFM.objects.filter(logger_id=device['id']).first()
            if row is None:
                found = list(MFM.objects.filter(active=True, table_name=m.table_name)[:2])
                if len(found) > 1:
                    report.error(m.path, f'several meters read "{m.table_name}" — fix them '
                                         f'in the admin first')
                    continue
                row = found[0] if found else None
        else:
            continue                                    # no id, bad table: reported
        key = ('pk', row.pk) if row is not None else ('table', m.table_name)
        plan = plans.get(key)
        if plan is None:
            plan = plans[key] = _Meter(path=m.path, mfm=row)
        for attr in ('name', 'table_name', 'schema'):
            value, held = getattr(m, attr), getattr(plan, attr)
            if value and held and value != held:
                report.error(m.path, f'{attr} {value!r} conflicts with {held!r} given for the '
                                     f'same meter at {plan.path}')
            elif value:
                setattr(plan, attr, value)
        if device is not None:
            plan.device = device
        for key_, value in m.nameplate.items():
            if key_ in plan.nameplate and plan.nameplate[key_] != value:
                report.error(m.path, f'nameplate {key_} {value} conflicts with '
                                     f'{plan.nameplate[key_]} for the same meter at {plan.path}')
            plan.nameplate.setdefault(key_, value)
        if panel is None:
            if plan.standalone is not None and plan.standalone is not cat:
                report.error(m.path, f'the meter is already under category '
                                     f'"{plan.standalone.label or plan.standalone.code}" at '
                                     f'{plan.path} — one category per standalone meter')
            plan.standalone = cat
        else:
            if any(p is panel for p, _path in plan.panels):
                report.error(m.path, 'the meter is already in this panel — one role per panel')
            plan.panels.append((panel, m.path))
        m.plan = plan

    by_device = {}
    for plan in plans.values():
        if plan.standalone is not None and plan.panels:
            report.error(plan.path, 'the meter is listed straight under a category and inside '
                                    f'a panel at {plan.panels[0][1]} — pick one')
        if plan.mfm is None and plan.device is None:
            report.error(plan.path, 'a new meter needs a "table_name"')
        if plan.device is not None:
            dev_id = plan.device['id']
            if dev_id in by_device:
                report.error(plan.path, f'table "{plan.table_name}" is also given to the meter '
                                        f'at {by_device[dev_id].path}')
            by_device[dev_id] = plan
            holder = MFM.objects.filter(logger_id=dev_id).first()
            if holder is not None and (plan.mfm is None or holder.pk != plan.mfm.pk):
                report.error(plan.path, f'table "{plan.table_name}" already belongs to MFM '
                                        f'"{holder.name}" ({holder.eid})')
    return plans


def _resolve_panels(categories, report):
    panels = [p for cat in categories for p in cat.panels]
    groups = Group.objects.in_bulk({p.mfm_id for p in panels if p.mfm_id})
    seen_groups, seen_names = {}, {}
    for p in panels:
        if p.mfm_id:
            g = groups.get(p.mfm_id)
            if g is None:
                report.error(p.path, f'panel {p.mfm_id} does not exist')
                continue
            if not g.is_panel:
                report.error(p.path, f'"{g.name}" ({p.mfm_id}) is a plain group, not a '
                                     f'distribution panel')
                continue
        else:
            found = list(Group.objects.filter(active=True, name__iexact=p.name)[:2])
            if len(found) > 1:
                report.error(p.path, f'several active groups are named "{p.name}" — give its '
                                     f'"mfm_id"')
                continue
            if found and not found[0].is_panel:
                report.error(p.path, f'"{p.name}" already exists as a plain group, not a '
                                     f'distribution panel')
                continue
            g = found[0] if found else (
                Group.objects.filter(active=False, is_panel=True, name__iexact=p.name)
                .order_by('-retired_at', '-id').first())
        p.group = g
        if g is not None:
            if g.pk in seen_groups:
                report.error(p.path, f'the panel is already listed at {seen_groups[g.pk]}')
            seen_groups.setdefault(g.pk, p.path)
        name = (p.name or (g.name if g else '')).casefold()
        if name and name in seen_names:
            report.error(p.path, f'a panel named "{p.name or g.name}" is already listed at '
                                 f'{seen_names[name]}')
        seen_names.setdefault(name, p.path)
    return panels


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------
def _assign(obj, fields, changes):
    """Assign ``fields`` on ``obj``; record 'field: old → new' for real changes."""
    for name, value in fields.items():
        old = getattr(obj, name)
        if old != value:
            setattr(obj, name, value)
            changes.append(f'{name}: {old if old not in (None, "") else "—"} → '
                           f'{value if value not in (None, "") else "—"}')


def _apply_schemas(schemas, report):
    rows = {}
    for key, item in schemas.items():
        if isinstance(item, LoggerSchema):
            rows[key] = item
            continue
        try:
            schema, created, _n = install_schema(
                item.spec, version=item.version, timestamp_column=item.timestamp_column,
                name=item.name, description=item.description)
        except ValueError as exc:
            report.error(item.path, str(exc))
            continue
        rows[key] = schema
        report.change('schema', 'create' if created else 'unchanged',
                      f'{schema.code} v{schema.version}', item.path,
                      f'{len(item.spec.fields)} fields' if created else '')
    return rows


def _apply_categories(categories, report):
    # Park every moving slug first: a swap between two categories must never
    # meet the unique index halfway.
    for cat in categories:
        if cat.row is not None and cat.row.slug != cat.slug:
            NavCategory.objects.filter(pk=cat.row.pk).update(slug=f'tmp-{cat.row.pk}-parking')
    for index, cat in enumerate(categories):
        row = cat.row
        if row is None:
            row = cat.row = NavCategory.objects.create(
                code=cat.code, label=cat.label, slug=cat.slug, sort_order=index,
                panel_kind=cat.panel_kind or 'distribution_panel',
                hide_when_empty=bool(cat.hide_when_empty))
            report.change('category', 'create', row.label, cat.path)
            continue
        changes, action = [], 'update' if row.active else 'reactivate'
        fields = {'label': cat.label or row.label, 'slug': cat.slug, 'sort_order': index,
                  'active': True}
        if cat.panel_kind:
            fields['panel_kind'] = cat.panel_kind
        if cat.hide_when_empty is not None:
            fields['hide_when_empty'] = cat.hide_when_empty
        _assign(row, fields, changes)
        row.save()
        report.change('category', 'unchanged' if not changes else action, row.label,
                      cat.path, '; '.join(changes))
    pushed = [cat.row.pk for cat in categories]
    for row in NavCategory.objects.filter(active=True).exclude(pk__in=pushed):
        row.active = False
        row.save(update_fields=['active', 'updated_at'])
        report.change('category', 'hide', row.label, '', 'not in the push')


def _retire(row, now):
    row.active, row.retired_at = False, now
    row.save(update_fields=['active', 'retired_at'])


def _apply_meter(plan, schemas, report):
    changes, m = [], plan.mfm
    if m is None:
        name = plan.name or plan.device['name']
        m = plan.mfm = MFM(name=name, slug=free_slug(name))
        action = 'create'
    else:
        action = 'update'
        if not m.active:
            m.active, m.retired_at, action = True, None, 'reactivate'
            if slug_owner(m.slug, m):
                m.slug = free_slug(m.name, m)
        if plan.name:
            _assign(m, {'name': plan.name}, changes)
    if plan.device is not None:
        bind_device(m, plan.device, changes)
    schema = schemas.get(plan.schema) if plan.schema else None
    if schema is not None and m.logger_schema_id != schema.pk:
        m.logger_schema = schema
        changes.append(f'logger_schema: → {schema.code} v{schema.version}')
    if plan.standalone is not None:
        _assign(m, {'nav_category': plan.standalone.code, 'nav_category_source': 'manual'},
                changes)
    m.save()
    cfg, _new = MfmConfig.objects.get_or_create(mfm=m)
    _assign(cfg, plan.nameplate, changes)
    cfg.save()
    ensure_mfm_equipment(m)
    if action == 'update' and not changes:
        action = 'unchanged'
    report.change('mfm', action, m.name, plan.path, '; '.join(changes))


def _apply_panel(p, index, categories, report):
    cat, g, changes = p.category, p.group, []
    if g is None:
        g = p.group = Group.objects.create(
            name=p.name, slug=free_slug(p.name), is_panel=True, nav_category=cat.code,
            nav_category_source='manual', sort_order=index)
        cfg = PanelConfig.objects.create(group=g, panel_kind=p.panel_kind or cat.row.panel_kind)
        action, changes = 'create', [f'under {cat.row.label}']
    else:
        action = 'update'
        moved = resolved_category(g, categories) != cat.code
        if not g.active:
            g.active, g.retired_at, action = True, None, 'reactivate'
            if slug_owner(g.slug, g):
                g.slug = free_slug(g.name, g)
        _assign(g, {'name': p.name or g.name, 'nav_category': cat.code,
                    'nav_category_source': 'manual', 'sort_order': index}, changes)
        g.save()
        cfg, _new = PanelConfig.objects.get_or_create(group=g)
        # An explicit kind wins; otherwise a panel changing category takes the
        # new category's default kind, exactly like the admin dropdown.
        kind = p.panel_kind or (cat.row.panel_kind if moved else None)
        if kind:
            _assign(cfg, {'panel_kind': kind}, changes)
    _assign(cfg, p.nameplate, changes)
    cfg.save()
    ensure_panel_equipment(g)
    if action == 'update' and not changes:
        action = 'unchanged'
    report.change('panel', action, g.name, p.path, '; '.join(changes))

    wanted, slots = {}, {}
    for role, m in p.members:
        position = slots[role] = slots.get(role, -1) + 1
        wanted[m.plan.mfm.pk] = (role, position, m)
    for row in Membership.objects.filter(group=g).select_related('mfm'):
        if row.mfm_id not in wanted:
            row.delete()
            report.change('membership', 'delete', f'{row.mfm.name} → {g.name}', p.path)
            continue
        role, position, m = wanted.pop(row.mfm_id)
        moved = []
        _assign(row, {'role': role, 'position': position}, moved)
        if moved:
            row.save(update_fields=['role', 'position'])
        report.change('membership', 'update' if moved else 'unchanged',
                      f'{row.mfm.name} → {g.name} ({role})', m.path, '; '.join(moved))
    for mfm_pk, (role, position, m) in wanted.items():
        Membership.objects.create(group=g, mfm_id=mfm_pk, role=role, position=position)
        report.change('membership', 'create', f'{m.plan.mfm.name} → {g.name} ({role})', m.path)
    for _role, m in p.members:
        ensure_primary_reference(m.plan.mfm)


def _apply(categories, panels, plans, schemas, report, now):
    schemas = _apply_schemas(schemas, report)
    if not report.ok:
        return
    _apply_categories(categories, report)

    keep = {p.group.pk for p in panels if p.group is not None}
    for g in Group.objects.filter(active=True, is_panel=True).exclude(pk__in=keep):
        _retire(g, now)
        report.change('panel', 'retire', g.name, '', 'not in the push')

    for plan in plans.values():
        _apply_meter(plan, schemas, report)

    resolved = Categories()
    for cat in categories:
        for index, p in enumerate(cat.panels):
            _apply_panel(p, index, resolved, report)

    standalone = {plan.mfm.pk for plan in plans.values() if plan.standalone is not None}
    for m in (MFM.objects.filter(active=True, nav_category_source='manual')
              .exclude(nav_category='').exclude(pk__in=standalone)):
        report.change('mfm', 'unfile', m.name, '', f'nav_category: {m.nav_category} → —')
        m.nav_category = ''
        m.save(update_fields=['nav_category', 'updated_at'])

    # A plain group shows its meters as loose leaves; one still holding a meter
    # the push did not place would put it back in the sidebar.
    placed = standalone | {plan.mfm.pk for plan in plans.values() if plan.panels}
    stray = {}
    for row in (Membership.objects.filter(group__active=True, group__is_panel=False,
                                          mfm__active=True)
                .exclude(mfm_id__in=placed).select_related('group', 'mfm')):
        stray.setdefault(row.group, []).append(row.mfm.name)
    for g, names in stray.items():
        _retire(g, now)
        report.change('group', 'retire', g.name, '',
                      f'would still show {", ".join(sorted(names)[:5])}'
                      f'{" …" if len(names) > 5 else ""}')


def run_push(body, *, commit=False):
    """Validate and apply a nav push. Writes only when ``commit`` and the whole
    push is clean; the returned report carries the resulting ``nav``."""
    report = Report()
    categories, definitions = parse(body, report)
    try:
        with transaction.atomic():
            # Every check runs before anything is written, so one push reports
            # all of its errors at once.
            _resolve_categories(categories, report)
            plans = _resolve_meters(categories, report)
            refs = {}
            for cat in categories:
                for m in [*cat.meters, *(m for p in cat.panels for _r, m in p.members)]:
                    if m.schema:
                        refs.setdefault(m.schema, f'{m.path}.schema')
            schemas = _resolve_schemas(definitions, refs, report)
            panels = _resolve_panels(categories, report)
            if report.ok:
                _apply(categories, panels, plans, schemas, report, timezone.now())
            if report.ok:
                report.nav = build_tree()
            if not (commit and report.ok):
                transaction.set_rollback(True)
    except IntegrityError as exc:
        report.error('', f'The database rejected the push: {exc}')
        report.nav = None
    report.committed = commit and report.ok
    if report.committed:
        from core.services import reset_energy_directions
        reset_energy_directions()
    return report
