"""LoggerFast -> beta rows.

Three pure-ish stages, deliberately split:

    fetch(client, dsn_by_target) -> DesiredState   ALL HTTP, no DB
    diff(desired)               -> Diff            DB reads only, never writes
    apply(desired, diff)        -> Summary         ONE transaction, no HTTP

Logger ids are the upsert keys and rows are soft-retired, never deleted, so the
ids the frontend puts in URLs survive a rename, a move or a disappearance.

Rows with NO logger id were built in the admin. The importer never updates,
retires, re-slugs or strips the memberships/twins/edges of such a row, and
reserves its slug (``manual_slugs``) so an imported row cannot take it.

Contract: a second run against unchanged logger state reports create 0 /
update 0 / retire 0 for every entity.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from core.models import (DataSource, FeedEdge, Group, Membership, MFM,
                         MfmConfig, NavCategory, PanelConfig)
from core.nav_categories import (LOW_CONFIDENCE, NAV_CATEGORIES,
                                 NAV_CATEGORY_LABEL, NAV_CATEGORY_SLUGS,
                                 PANEL_KIND_CATEGORY, classify)

# logger role -> our role; a plain group carries no role at all.
ROLE_MAP = {'incomer': 'incoming', 'outgoer': 'outgoing', 'spare': 'spare'}
# key in dsn_by_target for the DSN that is not tied to a named dbTargetId.
DEFAULT_DSN_KEY = '*'
ENTITIES = ('data_sources', 'mfms', 'bindings', 'groups', 'memberships', 'twins',
            'edges', 'categories')
_SLUG_BASE_MAX = 100          # leaves room for the "-<parent>" / "-2" suffixes
_SLUG_MAX = 120               # SlugField(max_length=120)
_DETAIL_CAP = 40              # entries listed per entity in the text report


# --------------------------------------------------------------------------
# slugs — tree-wide unique, deterministic, group slugs reserved before MFM ones
# --------------------------------------------------------------------------
def _slug(value):
    return re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-') or 'item'


def _uniq(used, base):
    """Append -2/-3 until free."""
    candidate, i = base, 2
    while candidate in used:
        candidate, i = f'{base}-{i}', i + 1
    used.add(candidate)
    return candidate


def _tree_slug(used, name, parent_slug=None):
    """Clean slug, then ``-<parent-slug>``, then a numeric counter."""
    base = _slug(name)[:_SLUG_BASE_MAX]
    if base not in used:
        used.add(base)
        return base
    if parent_slug:
        candidate = f'{base}-{parent_slug}'[:_SLUG_MAX]
        if candidate not in used:
            used.add(candidate)
            return candidate
        return _uniq(used, candidate)
    return _uniq(used, base)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value):
    return '' if value is None else str(value)


# --------------------------------------------------------------------------
# stage 1 — fetch / build
# --------------------------------------------------------------------------
@dataclass
class DesiredState:
    """Everything the logger says, already mapped to beta field names.

    ``twins``/``edges`` are None when the run skipped that call, which means
    "leave what is in the DB alone" rather than "there are none".
    """
    groups: list = field(default_factory=list)        # top-down: parents first
    mfms: list = field(default_factory=list)
    memberships: list = field(default_factory=list)
    bindings: dict = field(default_factory=dict)      # mfm logger_id -> binding dict
    data_sources: list = field(default_factory=list)
    twins: dict = None                                # group logger_id -> [logger_id]
    edges: list = None
    problems: list = field(default_factory=list)
    ungrouped_mfm_count: int = 0
    fetched_at: object = None


def manual_slugs():
    """Active slugs of rows built in the admin — pass to ``fetch`` so the
    importer's tree-wide slug allocation steps around them. User-defined
    category slugs are reserved the same way."""
    return {*Group.objects.filter(active=True, logger_id=None).values_list('slug', flat=True),
            *MFM.objects.filter(active=True, logger_id=None).values_list('slug', flat=True),
            *NavCategory.objects.values_list('slug', flat=True)}


def fetch(client, dsn_by_target, *, include_twins=True, include_edges=True,
          reserved_slugs=()):
    """Read the logger over HTTP and map it. No DB access at all."""
    devices = client.devices()
    tables = client.tables(status='migrated')
    tree = client.groups_tree()
    twins = None
    if include_twins:
        twins = {gid: [t.get('id') for t in (client.group_twins(gid) or []) if t.get('id')]
                 for gid in _tree_ids(tree)}
    edges = client.device_edges() if include_edges else None
    return build(tree, devices, tables, dsn_by_target, twins=twins, edges=edges,
                 reserved_slugs=reserved_slugs)


def _tree_ids(nodes):
    """Every group id in the tree, top-down."""
    out = []
    for node in nodes or []:
        gid = node.get('id')
        if gid:
            out.append(gid)
        out.extend(_tree_ids(node.get('children')))
    return out


def build(tree, devices, tables, dsn_by_target, *, twins=None, edges=None,
          reserved_slugs=()):
    """Pure mapping of the four API payloads into a DesiredState."""
    problems = []
    dev_by_id = {d['id']: d for d in (devices or []) if d.get('id')}

    # PIN: the nav category slugs are reserved BEFORE any group slug, so a
    # LoggerFast group literally named "HVAC Panels" cannot steal 'hvac-panels'
    # from the category and send the FE's slug->node fallback to the wrong node.
    # backend2 hit exactly this class of bug (backend2/core/views.py:185-196).
    # Admin-built rows' slugs are reserved alongside for the same reason.
    used_slugs = set(NAV_CATEGORY_SLUGS) | set(reserved_slugs)
    groups, memberships, first_group_slug = [], [], {}
    _walk(tree or [], None, None, groups, memberships, used_slugs,
          first_group_slug, dev_by_id, problems)

    # group slugs are all reserved above, so an MFM never steals one
    grouped = list(first_group_slug)
    ungrouped = sorted((d for d in dev_by_id if d not in first_group_slug),
                       key=lambda d: (_text(dev_by_id[d].get('name')), d))
    mfms = []
    for dev_id in grouped + ungrouped:
        dev = dev_by_id[dev_id]
        name = _text(dev.get('name')) or dev_id
        slug = _tree_slug(used_slugs, dev.get('name') or dev_id,
                          first_group_slug.get(dev_id))
        _kind, category, token = classify(slug, name)
        mfms.append({
            'logger_id': dev_id,
            'name': name,
            'slug': slug,
            'protocol': _text(dev.get('protocol')),
            'gateway_id': _text(dev.get('gatewayId')),
            'unit_id': _int_or_none(dev.get('unitId')),
            'port': _int_or_none(dev.get('port')),
            'logger_status': _text(dev.get('status')),
            # A meter only ever carries the nav_category axis: panel_kind lives
            # on PanelConfig, which belongs to a Group, not to a device.
            'nav_category': category,
            'nav_category_token': token,
        })

    bindings, data_sources = _bindings(tables, dev_by_id, dsn_by_target or {}, problems)

    norm_twins = None
    if twins is not None:
        known = {g['logger_id'] for g in groups}
        norm_twins = {}
        for group in groups:
            gid = group['logger_id']
            ids = {t.get('id') if isinstance(t, dict) else t
                   for t in (twins.get(gid) or [])}
            norm_twins[gid] = sorted(t for t in ids if t in known and t != gid)
    norm_edges = None
    if edges is not None:
        norm_edges = _edges(edges, dev_by_id, problems)

    return DesiredState(
        groups=groups, mfms=mfms, memberships=memberships, bindings=bindings,
        data_sources=data_sources, twins=norm_twins, edges=norm_edges,
        problems=problems, ungrouped_mfm_count=len(ungrouped),
        fetched_at=timezone.now())


def _walk(nodes, parent_logger_id, parent_slug, groups, memberships, used,
          first_group_slug, dev_by_id, problems):
    """Depth-first, parents before children, siblings in the order the API sent."""
    for node in nodes or []:
        gid = node.get('id')
        if not gid:
            problems.append('group without an id in /groups/tree — skipped')
            continue
        name = _text(node.get('name')) or gid
        is_panel = bool(node.get('isPanel'))
        slug = _tree_slug(used, name, parent_slug)
        panel_kind, category, token = classify(slug, name)
        groups.append({
            'logger_id': gid,
            'name': name,
            'slug': slug,
            'parent_logger_id': parent_logger_id,
            'sort_order': _int_or_none(node.get('sortOrder')) or 0,
            'is_panel': is_panel,
            # The two placement axes, seeded from the name. panel_kind only
            # means anything for a panel — a plain group carries no PanelConfig.
            'panel_kind': panel_kind if is_panel else '',
            'nav_category': category,
            'nav_category_token': token,
        })
        for position, dev in enumerate(node.get('devices') or []):
            dev_id = dev.get('id')
            if not dev_id:
                continue
            if dev_id not in dev_by_id:
                problems.append(f'{name}: device {dev_id} is in the group tree but not in '
                                f'/devices — membership skipped')
                continue
            raw = (_text(dev.get('role')).strip().lower() or None)
            if is_panel:
                # PIN 2026-09-09: a role violation is REPORTED and the membership is
                # skipped — writing it would create a row our own Membership.clean()
                # rejects. The command exits 2 so the logger gets fixed.
                if raw is None:
                    problems.append(f'{name} is a panel but {dev_id} has no role — '
                                    f'membership skipped')
                    continue
                role = ROLE_MAP.get(raw)
                if role is None:
                    problems.append(f'{name}: unknown role {raw!r} on {dev_id} — '
                                    f'membership skipped')
                    continue
            else:
                if raw is not None:
                    problems.append(f'{name} is not a panel but {dev_id} carries role '
                                    f'{raw!r} — membership skipped')
                    continue
                role = None
            memberships.append({'group_logger_id': gid, 'mfm_logger_id': dev_id,
                                'role': role, 'position': position})
            first_group_slug.setdefault(dev_id, slug)
        _walk(node.get('children'), gid, slug, groups, memberships, used,
              first_group_slug, dev_by_id, problems)


def _bindings(tables, dev_by_id, dsn_by_target, problems):
    """Per device: the one migrated table, or none/ambiguous. Plus the DataSources."""
    by_device = {}
    for row in tables or []:
        if (_text(row.get('status')).lower() or 'migrated') != 'migrated':
            continue
        dev_id = row.get('deviceId') or row.get('device_id')
        if dev_id:
            by_device.setdefault(dev_id, []).append(row)

    bindings, target_use = {}, {}
    for dev_id in sorted(dev_by_id):
        rows = sorted(by_device.get(dev_id, []), key=lambda r: _text(r.get('id')))
        names = sorted({_text(r.get('name')) for r in rows if r.get('name')})
        label = _text(dev_by_id[dev_id].get('name')) or dev_id
        if not names:
            bindings[dev_id] = {'table_name': '', 'logger_table_id': '',
                                'db_target_id': '', 'binding': 'none'}
        elif len(names) > 1:
            # PIN 2026-09-09: ambiguous clears the binding rather than guessing;
            # the operator picks one by hand, which flips it to 'manual' for good.
            problems.append(f'{label} ({dev_id}) has {len(names)} distinct migrated '
                            f'tables ({", ".join(names)}) — binding left ambiguous')
            bindings[dev_id] = {'table_name': '', 'logger_table_id': '',
                                'db_target_id': '', 'binding': 'ambiguous'}
        else:
            row = next(r for r in rows if _text(r.get('name')) == names[0])
            target = _text(row.get('dbTargetId') or (row.get('dbTarget') or {}).get('id'))
            bindings[dev_id] = {'table_name': names[0],
                                'logger_table_id': _text(row.get('id')),
                                'db_target_id': target, 'binding': 'auto'}
            target_use[target] = target_use.get(target, 0) + 1

    resolved = {t: dsn_by_target[t] for t in target_use if t in dsn_by_target}
    unresolved = [t for t in sorted(target_use) if t not in resolved]
    default_dsn = dsn_by_target.get(DEFAULT_DSN_KEY)
    if default_dsn and len(unresolved) == 1:
        resolved[unresolved.pop()] = default_dsn

    data_sources = []
    for target in sorted(target_use):
        if target in resolved:
            data_sources.append({
                'logger_db_target_id': target,
                'name': f'logger_{target}' if target else 'logger_default',
                'dsn': resolved[target],
            })
        else:
            problems.append(f'dbTargetId {target!r} has no DSN — set '
                            f'LOGGER_TIMESERIES_DSN_{target}; {target_use[target]} '
                            f'meter(s) stay unbound')
    return bindings, data_sources


def _edges(edges, dev_by_id, problems):
    """Keyed on (from, to) like the logger's own table; edge_type is a field."""
    out = {}
    for edge in edges or []:
        src, dst = edge.get('fromDeviceId'), edge.get('toDeviceId')
        if not src or not dst:
            continue
        if src not in dev_by_id or dst not in dev_by_id:
            problems.append(f'edge {src} -> {dst} references an unknown device — skipped')
            continue
        if src == dst:
            problems.append(f'self edge on {src} — skipped')
            continue
        out[(src, dst)] = _text(edge.get('edgeType')).lower() or 'feeds'
    return [{'from': s, 'to': d, 'edge_type': t} for (s, d), t in sorted(out.items())]


# --------------------------------------------------------------------------
# stage 2 — diff (DB reads only)
# --------------------------------------------------------------------------
@dataclass
class EntityDiff:
    create: list = field(default_factory=list)
    update: list = field(default_factory=list)
    retire: list = field(default_factory=list)
    unchanged: int = 0
    skipped: bool = False

    @property
    def touched(self):
        return bool(self.create or self.update or self.retire)

    def as_dict(self):
        return {'create': self.create, 'update': self.update, 'retire': self.retire,
                'unchanged': self.unchanged, 'skipped': self.skipped}


@dataclass
class Diff:
    entities: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    ungrouped_mfm_count: int = 0
    # The nav-category census: what the heuristic decided this run, so the
    # operator can judge it from --dry before anything is written. NOT named
    # 'categories': a real field would shadow the entity of that name, which
    # __getattr__ below resolves out of `entities`.
    category_census: dict = field(default_factory=dict)

    def __getattr__(self, name):
        """``diff.groups`` etc. read straight through to ``entities``."""
        entities = self.__dict__.get('entities') or {}
        if name in entities:
            return entities[name]
        raise AttributeError(name)

    @property
    def touched(self):
        return any(e.touched for e in self.entities.values())


def _changes(row, wanted):
    """{field: [old, new]} for the fields that actually differ."""
    return {f: [getattr(row, f), v] for f, v in wanted.items() if getattr(row, f) != v}


def _entry(key, label, changes=None):
    out = {'key': key, 'label': label}
    if changes:
        out['changes'] = {k: [_jsonable(a), _jsonable(b)] for k, (a, b) in changes.items()}
    return out


def _jsonable(value):
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _group_fields(want, group_pk_by_logger):
    # PIN: nav_category / panel_kind MUST NOT be listed here. This dict feeds
    # _changes() and then the unconditional setattr loop in _apply_groups, so a
    # category in it would be overwritten on EVERY run — including rows a human
    # pinned to 'manual'. Categories are written only by _apply_categories,
    # which filters on nav_category_source='auto' in the SQL WHERE.
    return {'name': want['name'], 'slug': want['slug'],
            'parent_id': group_pk_by_logger.get(want['parent_logger_id']),
            'sort_order': want['sort_order'], 'is_panel': want['is_panel'],
            'active': True}


def _mfm_fields(want):
    # PIN: nav_category MUST NOT be listed here — see _group_fields above.
    return {'name': want['name'], 'slug': want['slug'], 'protocol': want['protocol'],
            'gateway_id': want['gateway_id'], 'unit_id': want['unit_id'],
            'port': want['port'], 'logger_status': want['logger_status'], 'active': True}


def _category_subjects(desired, db_groups, db_mfms):
    """``(want, db_row_or_None)`` for every row that carries a nav_category —
    groups first, then meters, so the report reads in tree order."""
    for want in desired.groups:
        yield want, db_groups.get(want['logger_id'])
    for want in desired.mfms:
        yield want, db_mfms.get(want['logger_id'])


def _binding_fields(binding, ds_id):
    return {'table_name': binding['table_name'],
            'logger_table_id': binding['logger_table_id'],
            'table_binding': binding['binding'],
            'data_source_id': ds_id}


def _imported_memberships():
    """Memberships the logger owns — both ends carry a logger id."""
    return Membership.objects.exclude(group__logger_id=None).exclude(mfm__logger_id=None)


def _imported_edges():
    """Feed edges the logger owns — both meters carry a logger id."""
    return FeedEdge.objects.exclude(from_mfm__logger_id=None).exclude(to_mfm__logger_id=None)


def diff(desired):
    """Compare the DesiredState with the DB. Reads only — never writes."""
    out = Diff(problems=list(desired.problems),
               ungrouped_mfm_count=desired.ungrouped_mfm_count)
    for name in ENTITIES:
        out.entities[name] = EntityDiff()
    # readable labels for the report; the key stays the logger id
    gname = {g['logger_id']: g['name'] for g in desired.groups}
    mname = {m['logger_id']: m['name'] for m in desired.mfms}

    # --- data sources (never retired: shared rows behind a PROTECT FK) ------
    db_ds = list(DataSource.objects.all())
    ds_by_target = {d.logger_db_target_id: d for d in db_ds if d.logger_db_target_id}
    ds_by_dsn = {d.dsn: d for d in db_ds}
    ds_pk_by_target = {}
    e = out.entities['data_sources']
    for want in desired.data_sources:
        row = ds_by_target.get(want['logger_db_target_id']) or ds_by_dsn.get(want['dsn'])
        if row is None:
            e.create.append(_entry(want['logger_db_target_id'], want['dsn']))
            continue
        ds_pk_by_target[want['logger_db_target_id']] = row.pk
        changes = _changes(row, {'dsn': want['dsn'],
                                 'logger_db_target_id': want['logger_db_target_id']})
        (e.update.append(_entry(want['logger_db_target_id'], row.name, changes))
         if changes else _bump(e))

    # --- MFMs ---------------------------------------------------------------
    db_mfms = {m.logger_id: m for m in MFM.objects.exclude(logger_id=None)}
    e = out.entities['mfms']
    for want in desired.mfms:
        row = db_mfms.get(want['logger_id'])
        if row is None:
            e.create.append(_entry(want['logger_id'], want['name']))
            continue
        changes = _changes(row, _mfm_fields(want))
        e.update.append(_entry(want['logger_id'], row.name, changes)) if changes else _bump(e)
    wanted_mfm_ids = {m['logger_id'] for m in desired.mfms}
    for logger_id, row in db_mfms.items():
        if row.active and logger_id not in wanted_mfm_ids:
            e.retire.append(_entry(logger_id, row.name))

    # --- table bindings (manual rows are never touched) ---------------------
    e = out.entities['bindings']
    for want in desired.mfms:
        logger_id = want['logger_id']
        binding = desired.bindings.get(logger_id)
        if binding is None:
            continue
        row = db_mfms.get(logger_id)
        if row is not None and row.table_binding == 'manual':
            _bump(e)
            continue
        ds_id = ds_pk_by_target.get(binding['db_target_id'])
        fields = _binding_fields(binding, ds_id)
        if row is None:
            if binding['binding'] != 'none' or ds_id is not None:
                e.create.append(_entry(logger_id, f"{want['name']} -> "
                                                  f"{binding['table_name'] or binding['binding']}"))
            else:
                _bump(e)
            continue
        changes = _changes(row, fields)
        (e.update.append(_entry(logger_id, row.name, changes)) if changes else _bump(e))

    # --- groups -------------------------------------------------------------
    db_groups = {g.logger_id: g for g in Group.objects.exclude(logger_id=None)}
    group_pk_by_logger = {lid: g.pk for lid, g in db_groups.items()}
    e = out.entities['groups']
    for want in desired.groups:
        row = db_groups.get(want['logger_id'])
        if row is None:
            e.create.append(_entry(want['logger_id'], want['name']))
            continue
        changes = _changes(row, _group_fields(want, group_pk_by_logger))
        (e.update.append(_entry(want['logger_id'], row.name, changes)) if changes else _bump(e))
    wanted_group_ids = {g['logger_id'] for g in desired.groups}
    for logger_id, row in db_groups.items():
        if row.active and logger_id not in wanted_group_ids:
            e.retire.append(_entry(logger_id, row.name))

    # --- nav categories (manual rows are never touched) ---------------------
    # Mirrors the table-binding block above: the heuristic seeds, a human
    # overrides, the importer never touches it again.
    e = out.entities['categories']
    for want, row in _category_subjects(desired, db_groups, db_mfms):
        new = want['nav_category']
        if row is None:
            (e.create.append(_entry(want['logger_id'], want['name'],
                                    {'nav_category': ['', new]}))
             if new else _bump(e))
            continue
        if row.nav_category_source == 'manual':
            _bump(e)
            continue
        if not new and row.nav_category:
            # The heuristic lost a match it used to have (token table edited, or
            # the logger renamed the row). NEVER blank a stored value on that
            # basis — keep it and say so.
            out.problems.append(
                f'{row.name}: no longer matches any category token — '
                f'keeping {row.nav_category!r}')
            _bump(e)
            continue
        if row.nav_category != new:
            e.update.append(_entry(want['logger_id'], row.name,
                                   {'nav_category': [row.nav_category, new]}))
        else:
            _bump(e)

    # --- memberships --------------------------------------------------------
    e = out.entities['memberships']
    db_members = {}
    for m in _imported_memberships().select_related('group', 'mfm'):
        db_members[(m.group.logger_id, m.mfm.logger_id)] = m
    wanted_members = {(m['group_logger_id'], m['mfm_logger_id']): m
                      for m in desired.memberships}
    for key, want in wanted_members.items():
        row = db_members.get(key)
        label = f'{gname.get(key[0], key[0])}/{mname.get(key[1], key[1])}'
        if row is None:
            e.create.append(_entry('/'.join(key), label))
            continue
        changes = _changes(row, {'role': want['role'], 'position': want['position']})
        (e.update.append(_entry('/'.join(key), label, changes)) if changes else _bump(e))
    for key, row in db_members.items():
        if key not in wanted_members:
            e.retire.append(_entry('/'.join(key), f'{row.group.slug}/{row.mfm.slug}'))

    # --- twins --------------------------------------------------------------
    e = out.entities['twins']
    if desired.twins is None:
        e.skipped = True
    else:
        db_twins = {}
        for g in Group.objects.exclude(logger_id=None).prefetch_related('twins'):
            db_twins[g.logger_id] = sorted(t.logger_id for t in g.twins.all() if t.logger_id)
        def _twin_label(logger_id, ids):
            return f'{gname.get(logger_id, logger_id)} twins: ' + \
                   (', '.join(gname.get(t, t) for t in ids) or '(none)')

        for logger_id, wanted in desired.twins.items():
            current = db_twins.get(logger_id, [])
            if current == sorted(wanted):
                _bump(e)
            elif not current:
                e.create.append(_entry(logger_id, _twin_label(logger_id, wanted)))
            elif not wanted:
                e.retire.append(_entry(logger_id, _twin_label(logger_id, current)))
            else:
                e.update.append(_entry(logger_id, _twin_label(logger_id, wanted),
                                       {'twins': [', '.join(current), ', '.join(wanted)]}))

    # --- edges --------------------------------------------------------------
    e = out.entities['edges']
    if desired.edges is None:
        e.skipped = True
    else:
        db_edges = {}
        for edge in _imported_edges().select_related('from_mfm', 'to_mfm'):
            db_edges[(edge.from_mfm.logger_id, edge.to_mfm.logger_id)] = edge
        wanted_edges = {(x['from'], x['to']): x['edge_type'] for x in desired.edges}
        for key, edge_type in wanted_edges.items():
            row = db_edges.get(key)
            label = f'{mname.get(key[0], key[0])} -> {mname.get(key[1], key[1])}'
            if row is None:
                e.create.append(_entry('->'.join(key), f'{label} ({edge_type})'))
            elif row.edge_type != edge_type:
                e.update.append(_entry('->'.join(key), label,
                                       {'edge_type': [row.edge_type, edge_type]}))
            else:
                _bump(e)
        for key, row in db_edges.items():
            if key not in wanted_edges:
                e.retire.append(_entry('->'.join(key),
                                       f'{row.from_mfm.name} -> {row.to_mfm.name}'))

    out.category_census = _census(desired)
    return out


def _census(desired):
    """What the heuristic decided, per category, plus the rows to eyeball.

    ``counts`` carries EVERY category even at zero: a zero next to a category
    the operator knows has panels is the fastest possible "the heuristic
    missed" signal, and it only reads that way if the row is always printed.
    """
    counts = {cid: {'panels': 0, 'loose': 0, 'feeders': 0}
              for cid, _label, _slug in NAV_CATEGORIES}
    counts[''] = {'panels': 0, 'loose': 0, 'feeders': 0}
    uncategorised, low_confidence = [], []

    cat_by_group = {}
    for want in desired.groups:
        cid = (PANEL_KIND_CATEGORY.get(want.get('panel_kind') or '')
               or want['nav_category'])
        cat_by_group[want['logger_id']] = cid
        counts.setdefault(cid, {'panels': 0, 'loose': 0, 'feeders': 0})
        if not want['is_panel']:
            continue                    # a plain group is not itself a node
        counts[cid]['panels'] += 1
        if not cid:
            uncategorised.append(_census_row(want, 'panel'))
        elif want['nav_category_token'] in LOW_CONFIDENCE:
            low_confidence.append(_census_row(want, 'panel', cid))

    # A meter inside a panel is placed BY ITS PANEL, so its feeder count belongs
    # to the panel's category, not to whatever its own name happens to match.
    panel_of = {m['mfm_logger_id']: m['group_logger_id']
                for m in desired.memberships if m.get('role')}
    grouped = {m['mfm_logger_id'] for m in desired.memberships}
    for want in desired.mfms:
        logger_id = want['logger_id']
        if logger_id in panel_of:
            cid = cat_by_group.get(panel_of[logger_id], '')
            counts.setdefault(cid, {'panels': 0, 'loose': 0, 'feeders': 0})
            counts[cid]['feeders'] += 1
            continue
        cid = want['nav_category']
        counts.setdefault(cid, {'panels': 0, 'loose': 0, 'feeders': 0})
        counts[cid]['loose'] += 1
        if not cid:
            # An UNGROUPED meter is not in the nav at all (it is already counted
            # as ungrouped_mfm_count), so filing it would change nothing — only
            # a loose meter that DOES render is worth an operator's attention.
            if logger_id in grouped:
                uncategorised.append(_census_row(want, 'meter'))
        elif want['nav_category_token'] in LOW_CONFIDENCE:
            low_confidence.append(_census_row(want, 'meter', cid))
    return {'counts': counts, 'uncategorised': uncategorised,
            'low_confidence': low_confidence}


def _census_row(want, kind, category=None):
    row = {'key': want['logger_id'], 'label': want['name'],
           'slug': want['slug'], 'kind': kind}
    if category is not None:
        row['category'] = category
        row['token'] = want['nav_category_token']
    return row


def _bump(entity):
    entity.unchanged += 1


# --------------------------------------------------------------------------
# stage 3 — apply (one transaction, no HTTP)
# --------------------------------------------------------------------------
@dataclass
class Summary:
    counts: dict = field(default_factory=dict)     # entity -> {action: n}
    config_rows: dict = field(default_factory=dict)
    applied_at: object = None

    def bump(self, entity, action, n=1):
        self.counts.setdefault(entity, {}).setdefault(action, 0)
        self.counts[entity][action] += n

    def get(self, entity, action):
        return self.counts.get(entity, {}).get(action, 0)

    def as_dict(self):
        return {'counts': self.counts, 'config_rows': self.config_rows,
                'applied_at': self.applied_at.isoformat() if self.applied_at else None}


def _park_slug(model, pk, kind):
    """Move a slug out of the way so the partial-unique index never sees a
    transient duplicate during a rename/swap/retire."""
    model.objects.filter(pk=pk).update(slug=f'tmp-{kind}-{pk}-slug-parking')


def apply(desired, diff, *, now=None):
    """Write the DesiredState in one transaction, in the plan's fixed order.

    ``diff`` is taken for the caller's convenience and for the report; apply
    re-reads the DB itself so it can never write a stale decision.
    """
    now = now or timezone.now()
    summary = Summary(applied_at=now)
    with transaction.atomic():
        ds_pk_by_target = _apply_data_sources(desired, summary)
        parked = _park_moving_slugs(desired)
        mfm_pk = _apply_mfms(desired, now, summary)
        _apply_bindings(desired, mfm_pk, ds_pk_by_target, summary)
        group_pk = _apply_groups(desired, now, summary)
        _apply_memberships(desired, group_pk, mfm_pk, summary)
        _apply_twins(desired, group_pk, summary)
        _apply_edges(desired, mfm_pk, summary)
        _retire_vanished(desired, now, parked, summary)
        _ensure_config_rows(summary)
        # LAST, and deliberately so, on both counts: _apply_mfms/_apply_groups
        # end in a bare row.save() that would revert a category written from a
        # different instance, and _seed_panel_kind needs the PanelConfig row
        # _ensure_config_rows has just created.
        _apply_categories(desired, group_pk, mfm_pk, summary)
        _ensure_equipment_rows()
    _reset_energy_directions()
    return summary


def _apply_data_sources(desired, summary):
    """One row per dbTargetId; adopts an operator-created row with the same DSN."""
    pk_by_target = {}
    for want in desired.data_sources:
        target, dsn = want['logger_db_target_id'], want['dsn']
        row = (DataSource.objects.filter(logger_db_target_id=target).first() if target
               else None) or DataSource.objects.filter(dsn=dsn).first()
        if row is None:
            name, i = want['name'], 2
            while DataSource.objects.filter(name=name).exists():
                name, i = f"{want['name']}-{i}", i + 1
            row = DataSource.objects.create(
                name=name, dsn=dsn, logger_db_target_id=target,
                description='Created by import_logger_hierarchy.')
            summary.bump('data_sources', 'created')
        else:
            changed = {}
            if row.dsn != dsn:
                changed['dsn'] = dsn
            if row.logger_db_target_id != target:
                changed['logger_db_target_id'] = target
            if changed:
                for key, value in changed.items():
                    setattr(row, key, value)
                row.save(update_fields=list(changed))
                summary.bump('data_sources', 'updated')
        pk_by_target[target] = row.pk
    return pk_by_target


def _park_moving_slugs(desired):
    """Park the slugs of rows whose slug moves, and of rows about to be retired.

    Returns {model_label: {pk: original_slug}} so the retire step can hand the
    old slug back to the (inactive) row.
    """
    parked = {'group': {}, 'mfm': {}}
    for model, kind, wanted in ((Group, 'group', desired.groups), (MFM, 'mfm', desired.mfms)):
        by_logger = {w['logger_id']: w['slug'] for w in wanted}
        for pk, logger_id, slug in (model.objects.filter(active=True)
                                    .exclude(logger_id=None)
                                    .values_list('pk', 'logger_id', 'slug')):
            want_slug = by_logger.get(logger_id)
            if want_slug is None:                       # vanishing: releases its slug
                parked[kind][pk] = slug
                _park_slug(model, pk, kind)
            elif want_slug != slug:
                _park_slug(model, pk, kind)
    return parked


def _apply_mfms(desired, now, summary):
    pk_by_logger = {}
    for want in desired.mfms:
        fields = _mfm_fields(want)
        row = MFM.objects.filter(logger_id=want['logger_id']).first()
        if row is None:
            row = MFM.objects.create(logger_id=want['logger_id'], imported_at=now, **fields)
            summary.bump('mfms', 'created')
        else:
            changed = _changes(row, fields)
            if not row.active:
                summary.bump('mfms', 'reactivated')
            for key, value in fields.items():
                setattr(row, key, value)
            row.retired_at = None
            row.imported_at = now
            row.save()
            if changed:
                summary.bump('mfms', 'updated')
        pk_by_logger[want['logger_id']] = row.pk
    return pk_by_logger


def _apply_bindings(desired, mfm_pk, ds_pk_by_target, summary):
    for want in desired.mfms:
        binding = desired.bindings.get(want['logger_id'])
        if binding is None:
            continue
        row = MFM.objects.get(pk=mfm_pk[want['logger_id']])
        if row.table_binding == 'manual':
            summary.bump('bindings', 'manual_kept')
            continue
        fields = _binding_fields(binding, ds_pk_by_target.get(binding['db_target_id']))
        changed = _changes(row, fields)
        if changed:
            for key, value in fields.items():
                setattr(row, key, value)
            row.save(update_fields=list(fields))
            summary.bump('bindings', 'bound' if binding['binding'] == 'auto' else 'cleared')


def _apply_groups(desired, now, summary):
    """Top-down, so a parent always exists before its children."""
    pk_by_logger = {}
    for want in desired.groups:
        fields = _group_fields(want, pk_by_logger)
        row = Group.objects.filter(logger_id=want['logger_id']).first()
        if row is None:
            row = Group.objects.create(logger_id=want['logger_id'], imported_at=now, **fields)
            summary.bump('groups', 'created')
        else:
            changed = _changes(row, fields)
            if not row.active:
                summary.bump('groups', 'reactivated')
            for key, value in fields.items():
                setattr(row, key, value)
            row.retired_at = None
            row.imported_at = now
            row.save()
            if changed:
                summary.bump('groups', 'updated')
        pk_by_logger[want['logger_id']] = row.pk
    return pk_by_logger


def _apply_categories(desired, group_pk, mfm_pk, summary):
    """Seed nav_category (and a panel's panel_kind) on rows nobody has pinned.

    PIN: the ``nav_category_source='auto'`` guard is in the SQL WHERE, not only
    in Python — apply() re-reads the DB so it can never write a stale decision,
    so a row pinned to 'manual' between diff() and apply() is refused by the
    database rather than merely skipped by us.
    """
    for model, wanted, pk_by_logger in ((Group, desired.groups, group_pk),
                                        (MFM, desired.mfms, mfm_pk)):
        for want in wanted:
            pk = pk_by_logger.get(want['logger_id'])
            if pk is None:
                continue
            row = model.objects.filter(pk=pk).values(
                'nav_category', 'nav_category_source').first()
            if row is None:
                continue
            if row['nav_category_source'] == 'manual':
                summary.bump('categories', 'manual_kept')
                continue
            new = want['nav_category']
            if not new and row['nav_category']:
                summary.bump('categories', 'kept_unmatched')   # never blank it
            elif new != row['nav_category']:
                model.objects.filter(pk=pk, nav_category_source='auto').update(
                    nav_category=new)
                summary.bump('categories',
                             'seeded' if not row['nav_category'] else 'recategorised')
            else:
                summary.bump('categories', 'kept' if new else 'uncategorised')
            if model is Group and want.get('panel_kind'):
                _seed_panel_kind(pk, want['panel_kind'], summary)


def _seed_panel_kind(group_pk, panel_kind, summary):
    """The structural half of the axis (HT/AMF/PCC/UPS), on the panel's config.

    Only ever promotes a row still sitting on the 'distribution_panel' default:
    anything else is a human's choice, or a previous seed the operator has since
    corrected, and must not be re-decided by the heuristic.
    """
    updated = PanelConfig.objects.filter(
        group_id=group_pk, panel_kind='distribution_panel').update(panel_kind=panel_kind)
    summary.bump('categories', 'panel_kind_seeded' if updated else 'panel_kind_kept')


def _apply_memberships(desired, group_pk, mfm_pk, summary):
    keep = set()
    for want in desired.memberships:
        gid, mid = group_pk.get(want['group_logger_id']), mfm_pk.get(want['mfm_logger_id'])
        if gid is None or mid is None:
            continue
        row, created = Membership.objects.update_or_create(
            group_id=gid, mfm_id=mid,
            defaults={'role': want['role'], 'position': want['position']})
        summary.bump('memberships', 'created' if created else 'kept')
        keep.add(row.pk)
    stale = _imported_memberships().exclude(pk__in=keep)
    deleted = stale.count()
    if deleted:
        stale.delete()
        summary.bump('memberships', 'deleted', deleted)


def _apply_twins(desired, group_pk, summary):
    if desired.twins is None:
        return
    for logger_id, wanted in desired.twins.items():
        pk = group_pk.get(logger_id)
        if pk is None:
            continue
        group = Group.objects.get(pk=pk)
        target = [group_pk[t] for t in wanted if t in group_pk]
        # a twin built in the admin is not the logger's to drop
        target += [t.pk for t in group.twins.all() if t.logger_id is None]
        if sorted(t.pk for t in group.twins.all()) != sorted(target):
            group.twins.set(target)
            summary.bump('twins', 'set')


def _apply_edges(desired, mfm_pk, summary):
    if desired.edges is None:
        return
    keep = set()
    for want in desired.edges:
        src, dst = mfm_pk.get(want['from']), mfm_pk.get(want['to'])
        if src is None or dst is None or src == dst:
            continue
        row = FeedEdge.objects.filter(from_mfm_id=src, to_mfm_id=dst).first()
        if row is None:
            row = FeedEdge.objects.create(from_mfm_id=src, to_mfm_id=dst,
                                          edge_type=want['edge_type'])
            summary.bump('edges', 'created')
        elif row.edge_type != want['edge_type']:
            row.edge_type = want['edge_type']
            row.save(update_fields=['edge_type'])
            summary.bump('edges', 'updated')
        keep.add(row.pk)
    stale = _imported_edges().exclude(pk__in=keep)
    deleted = stale.count()
    if deleted:
        stale.delete()
        summary.bump('edges', 'deleted', deleted)


def _retire_vanished(desired, now, parked, summary):
    """Soft-retire what the logger no longer has; keep the id and the config row."""
    wanted_groups = {g['logger_id'] for g in desired.groups}
    for group in (Group.objects.filter(active=True).exclude(logger_id=None)
                  .exclude(logger_id__in=wanted_groups)):
        Membership.objects.filter(group=group).delete()
        group.twins.clear()
        group.active = False
        group.retired_at = now
        group.slug = parked['group'].get(group.pk, group.slug)
        group.save(update_fields=['active', 'retired_at', 'slug'])
        summary.bump('groups', 'retired')

    wanted_mfms = {m['logger_id'] for m in desired.mfms}
    for mfm in (MFM.objects.filter(active=True).exclude(logger_id=None)
                .exclude(logger_id__in=wanted_mfms)):
        Membership.objects.filter(mfm=mfm).delete()
        FeedEdge.objects.filter(from_mfm=mfm).delete()
        FeedEdge.objects.filter(to_mfm=mfm).delete()
        mfm.active = False
        mfm.retired_at = now
        mfm.slug = parked['mfm'].get(mfm.pk, mfm.slug)
        mfm.save(update_fields=['active', 'retired_at', 'slug'])
        summary.bump('mfms', 'retired')


def _ensure_config_rows(summary):
    """One config row per active panel and per active meter. get_or_create ONLY —
    the nameplate data is ours and the importer must never overwrite it."""
    panels = 0
    for group in Group.objects.filter(active=True, is_panel=True):
        panels += int(PanelConfig.objects.get_or_create(group=group)[1])
    meters = 0
    for mfm in MFM.objects.filter(active=True):
        meters += int(MfmConfig.objects.get_or_create(mfm=mfm)[1])
    summary.config_rows = {'panel_configs_created': panels, 'mfm_configs_created': meters}


def _ensure_equipment_rows():
    """Add backend-owned equipment metadata after category/type seeding."""
    from core.equipment import (ensure_mfm_equipment, ensure_panel_equipment,
                                ensure_primary_reference)
    for group in Group.objects.filter(active=True, is_panel=True).select_related('config'):
        ensure_panel_equipment(group)
    for mfm in MFM.objects.filter(active=True).select_related('config', 'equipment'):
        ensure_mfm_equipment(mfm)
        ensure_primary_reference(mfm)


def _reset_energy_directions():
    """Drop services.py's cached direction registry (another lane owns that file)."""
    try:
        from core import services
    except ImportError:                                 # pragma: no cover - lane guard
        return
    reset = getattr(services, 'reset_energy_directions', None)
    if callable(reset):
        reset()


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def _entity_rows(result):
    for name in ENTITIES:
        yield name, result.entities[name]


def _census_lines(census):
    """The nav-category block: every category, then the rows to eyeball."""
    if not census:
        return []
    counts = census.get('counts') or {}
    lines = ['', '  nav categories (from the heuristic):']
    for cid, label, _slug in NAV_CATEGORIES:
        c = counts.get(cid) or {}
        lines.append(f'    {label:<20}{c.get("panels", 0):>4} panels'
                     f'{c.get("loose", 0):>6} loose{c.get("feeders", 0):>6} feeders')
    c = counts.get('') or {}
    lines.append(f'    {"— uncategorised —":<20}{c.get("panels", 0):>4} panels'
                 f'{c.get("loose", 0):>6} loose{c.get("feeders", 0):>6} feeders')

    for key, heading, fmt_row in (
            ('uncategorised', 'uncategorised (assign these in the admin)',
             lambda r: f'    ? {r["label"]} [{r["key"]}] slug={r["slug"]}'),
            ('low_confidence', 'low-confidence categorisations (review these)',
             lambda r: f'    ~ {r["label"]} [{r["key"]}] -> '
                       f'{NAV_CATEGORY_LABEL.get(r["category"], r["category"])} '
                       f'(matched {r["token"]!r})')):
        rows = census.get(key) or []
        if not rows:
            continue
        lines.append(f'  {heading} ({len(rows)}):')
        lines.extend(fmt_row(r) for r in rows[:_DETAIL_CAP])
        if len(rows) > _DETAIL_CAP:
            lines.append(f'    … and {len(rows) - _DETAIL_CAP} more')
    return lines


def report(diff, summary=None, fmt='text'):
    """Human (or --json) rendering of what changed and what was written."""
    if fmt == 'json':
        return json.dumps({
            'dry_run': summary is None,
            'entities': {n: e.as_dict() for n, e in _entity_rows(diff)},
            'problems': diff.problems,
            'ungrouped_mfm_count': diff.ungrouped_mfm_count,
            'categories': diff.category_census,
            'applied': summary.as_dict() if summary else None,
        }, indent=2)

    head = 'LoggerFast import — DRY RUN (nothing written)' if summary is None \
        else 'LoggerFast import — applied'
    lines = [head, '',
             f'  {"entity":<14}{"create":>8}{"update":>8}{"retire":>8}{"unchanged":>11}']
    for name, e in _entity_rows(diff):
        if e.skipped:
            lines.append(f'  {name:<14}{"skipped":>8}')
            continue
        lines.append(f'  {name:<14}{len(e.create):>8}{len(e.update):>8}'
                     f'{len(e.retire):>8}{e.unchanged:>11}')
    lines.append('')
    lines.append(f'  ungrouped MFMs: {diff.ungrouped_mfm_count}')
    lines.extend(_census_lines(diff.category_census))

    for name, e in _entity_rows(diff):
        if not e.touched:
            continue
        lines.append(f'  {name}:')
        for sign, entries in (('+', e.create), ('~', e.update), ('-', e.retire)):
            for item in entries[:_DETAIL_CAP]:
                detail = ''
                if item.get('changes'):
                    detail = '  ' + ', '.join(f'{k}: {a!r} -> {b!r}'
                                              for k, (a, b) in item['changes'].items())
                lines.append(f'    {sign} {item["label"]} [{item["key"]}]{detail}')
            if len(entries) > _DETAIL_CAP:
                lines.append(f'    … and {len(entries) - _DETAIL_CAP} more')

    if diff.problems:
        lines.append('')
        lines.append(f'  problems ({len(diff.problems)}):')
        lines.extend(f'    ! {p}' for p in diff.problems)

    if summary is not None:
        lines.append('')
        lines.append('  written:')
        for entity in ENTITIES:
            actions = summary.counts.get(entity)
            if actions:
                lines.append(f'    {entity}: ' +
                             ', '.join(f'{k} {v}' for k, v in sorted(actions.items())))
        cfg = summary.config_rows or {}
        lines.append(f'    config rows: panels {cfg.get("panel_configs_created", 0)}, '
                     f'meters {cfg.get("mfm_configs_created", 0)}')
    return '\n'.join(lines)
