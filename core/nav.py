"""EMS nav tree — a two-level sidebar over the user-editable categories.

    category (NavCategory rows: active, in sort order; hide_when_empty ones
      │       are omitted while they hold nothing)
      ├── panel      (Group.is_panel) -> Overview + Incoming/Outgoing/Spare
      └── loose leaf (an active MFM that no panel claims)

Intermediate non-panel Groups no longer render. They keep two jobs: they own
memberships (their meters become loose leaves) and they carry ``nav_category``
for their whole subtree. A meter in no active group becomes a loose leaf only
when a human filed it under a category (see ``_STANDALONE``).

There is NO per-category mode — backend2's ``mode: hierarchical|flat`` is gone.
Whether a node expands is derived per node from ``Group.is_panel``, so a
distribution panel gets its role groups wherever it is filed.

A panel's own category, strongest first: a hand-set ``nav_category``
(admin, workbook, push), then ``PanelConfig.panel_kind`` through the first
active category with that default kind, then an importer-guessed
``nav_category``; else the nearest ancestor's. Codes naming no active
category count as unset. Node shapes: see ``NAV_CONTRACT.md``.
"""
from django.db.models import Q

from core.config import resolve_panel_config
from core.config_defaults import defaults_for
from core.constants import FEEDER_ID_OFFSET
from core.models import Group, Membership, MFM, NavCategory

# FE module basePath the category slugs hang under (pathOverride).
_MODULE = '/electrical'
# Role groups, in display order; empty ones are omitted.
_ROLES = [('incoming', 'Incoming'), ('outgoing', 'Outgoing'), ('spare', 'Spare')]
# A meter in NO active group, filed straight under a category by hand.
# 'manual' is the point: the admin pins every category it writes, while an
# imported ungrouped device carries only the importer's name GUESS ('auto') and
# stays out. A membership of a retired group does not count as a group.
_HAND_FILED = Q(nav_category_source='manual') & ~Q(nav_category='')
_IN_ACTIVE_GROUP = Q(memberships__group__active=True)


class Categories:
    """The active categories, loaded once per tree/request."""

    def __init__(self, rows=None):
        self.rows = list(NavCategory.objects.filter(active=True)) if rows is None else rows
        self.codes = {c.code for c in self.rows}
        self.label = {c.code: c.label for c in self.rows}
        # panel_kind -> the FIRST active category defaulting to it
        self.by_kind = {}
        for c in self.rows:
            if c.panel_kind != 'distribution_panel':
                self.by_kind.setdefault(c.panel_kind, c.code)

    def known(self, code):
        return code if code in self.codes else None


def panel_axes(code):
    """Category code -> ``(panel_kind, nav_category)`` for a panel filed there
    by hand: the category's default kind, and the code itself — always set, so
    two categories sharing a kind stay distinct."""
    row = NavCategory.objects.filter(code=code).first()
    return (row.panel_kind if row else 'distribution_panel'), code


def category_label(code, categories=None):
    if categories is None:
        row = NavCategory.objects.filter(code=code).first()
        return row.label if row else code
    return categories.label.get(code, code)


def _kv(volts):
    """Volts -> kV at 2 dp (backend2 arithmetic), None when unset."""
    return round(float(volts) / 1000, 2) if volts else None


def _mfm_nominal_kv(mfm):
    """A meter's own nominal, LINE-TO-LINE, in kV."""
    # PIN 2026-09-09: nav reads the RAW L-L nominal (MfmConfig override else the
    # device-type default), NOT resolve_mfm_config() — that ships L-N (÷√3, 1 dp)
    # for the per-phase meter screens, and ×√3 back re-introduces the rounding
    # artifact. Panels use resolve_panel_config(), which is already L-L (BE-58).
    cfg = getattr(mfm, 'config', None)
    volts = getattr(cfg, 'nominal_voltage_v', None)
    if volts is None:
        device_type = getattr(cfg, 'device_type', None) or 'lt_panel'
        volts = defaults_for(device_type).get('nominal_voltage_v')
    return _kv(volts)


def _group_category(group, categories):
    """A group's OWN category, or None (see the module docstring for order).

    ``panel_kind`` only means anything on a panel — a plain group has no
    PanelConfig — and its 'distribution_panel' default maps to nothing.
    """
    own = categories.known(group.nav_category)
    if own and group.nav_category_source == 'manual':
        return own
    if group.is_panel:
        cfg = getattr(group, 'config', None)
        cid = categories.by_kind.get(getattr(cfg, 'panel_kind', '') or '')
        if cid:
            return cid
    return own


def resolved_category(group, categories=None):
    """One group's category — own value, else the nearest ancestor's — for the
    admin forms. ``build_tree`` uses the batched ``_category_index`` instead."""
    categories = categories or Categories()
    seen = set()
    while group is not None and group.pk not in seen:
        seen.add(group.pk)
        cid = _group_category(group, categories)
        if cid:
            return cid
        group = group.parent
    return None


def panel_category_index():
    """``{Group.id: category code or None}`` for every group, batched."""
    groups = list(Group.objects.select_related('config'))
    return _category_index(groups, Categories())


def _category_index(groups, categories):
    """``{Group.id: category id or None}`` — own category, else the nearest
    ancestor that has one.

    ``groups`` MUST include retired rows. nav has always guaranteed that
    soft-retiring a section never hides the panels under it; if the walk
    stopped at an inactive parent, retiring one section would silently
    re-bucket its whole subtree. ``_retire_vanished`` preserves ``parent_id``,
    so the chain is intact. Memoised: O(n), no queries, cycle-safe.
    """
    own = {g.id: _group_category(g, categories) for g in groups}
    parent = {g.id: g.parent_id for g in groups}
    memo = {}

    for start in own:
        chain, seen, gid = [], set(), start
        while True:
            if gid is None or gid in seen:      # root reached, or a parent cycle
                cid = None
                break
            if gid in memo:
                cid = memo[gid]
                break
            if own.get(gid):
                cid = own[gid]
                break
            seen.add(gid)
            chain.append(gid)
            gid = parent.get(gid)
        for node in chain:
            memo[node] = cid
        if gid is not None:
            memo[gid] = cid
    return memo


def _member_leaf(panel_id, membership, stats):
    """One metered leaf inside a panel role group (one per Membership)."""
    mfm = membership.mfm
    eid = FEEDER_ID_OFFSET + mfm.id
    stats['leaves'] += 1
    if mfm.data_source_id and mfm.table_name:
        stats['matched'].add(mfm.id)
    return {
        'id': f'{panel_id}-mfm-{eid}',
        'label': mfm.name,
        'slug': mfm.slug,
        'mfm_id': eid,
        'mfm_name': mfm.name,
        'key': mfm.slug,
        'role': membership.role,
        'logger_device_id': mfm.logger_id,
        'mfm_row': mfm.sheet_row,
        'breaker_type': getattr(getattr(mfm, 'breaker', None), 'breaker_type', None),
        'nominal_kv': _mfm_nominal_kv(mfm),
        'distribution_panel': False,
    }


def _loose_leaf(mfm, category, stats):
    """A meter attached straight to a plain group — the flat single-MFM leaf.

    At most ONE per meter tree-wide, and never for a meter a panel claims.
    """
    eid = FEEDER_ID_OFFSET + mfm.id
    stats['leaves'] += 1
    if mfm.data_source_id and mfm.table_name:
        stats['matched'].add(mfm.id)
    return {
        'id': f'eq-mfm-{eid}',
        'label': mfm.name,
        'slug': mfm.slug,
        'key': mfm.slug,
        'kind': 'panel',
        'distribution_panel': False,
        'bus_kv': _mfm_nominal_kv(mfm),
        'model_slug': None,
        'mfm_id': eid,
        'mfm_name': mfm.name,
        'mfm_ids': [eid],
        'logger_device_id': mfm.logger_id,
        'mfm_row': mfm.sheet_row,
        'breaker_type': getattr(getattr(mfm, 'breaker', None), 'breaker_type', None),
        'category': category,
    }


def _parent_label(group, by_id):
    """The nearest ACTIVE ancestor's name, or None at a root.

    Flattening to two levels throws away "which room is this panel in", and
    CMD_V2's nameplate falls back to a name-regex guess without it — backend2
    documented that guess mis-reading 'crp-panel-03' as "PCC Panel 03". The
    ancestors are already in memory, so recovering it costs nothing.
    """
    seen, gid = set(), group.parent_id
    while gid is not None and gid not in seen:
        seen.add(gid)
        parent = by_id.get(gid)
        if parent is None:
            return None
        if parent.active:
            return parent.name
        gid = parent.parent_id
    return None


def _panel_node(group, memberships, category, by_id, stats):
    # Keyed on Group.id, not logger_id: a panel built in the admin has no
    # logger id, and Group.id is already the stable id the screens address.
    panel_id = f'grp-{group.id}'
    by_role = {}
    for m in memberships:
        by_role.setdefault(m.role, []).append(m)
    # Overview is emitted for EVERY panel, uniformly — a panel with no
    # aggregate screen yet still gets the node.
    children = [{'id': f'{panel_id}-overview', 'label': 'Overview', 'slug': 'overview'}]
    for role, label in _ROLES:
        rows = by_role.get(role)
        if not rows:
            continue
        children.append({
            'id': f'{panel_id}-{role}',
            'label': label,
            'slug': role,
            'group': True,
            'alwaysOpen': True,
            'children': [_member_leaf(panel_id, m, stats) for m in rows],
        })
    node = {
        'id': panel_id,
        'label': group.name,
        'slug': group.slug,
        'key': group.slug,
        'kind': 'panel',
        'distribution_panel': True,
        'mfm_id': group.id,          # panels are addressed by Group.id (< offset)
        'mfm_name': group.name,
        'bus_kv': _kv(resolve_panel_config(group).get('nominal_voltage_v')),
        'model_slug': None,
        'logger_id': group.logger_id,
        'twins': [t.slug for t in group.twins.all() if t.active],
        'category': category,
        'children': children,
    }
    label = _parent_label(group, by_id)
    if label:
        node['parent_label'] = label
    return node


def standalone_mfms():
    """Active meters in no active group, filed under a category by hand."""
    return MFM.objects.filter(_HAND_FILED, active=True).exclude(_IN_ACTIVE_GROUP)


def ungrouped_mfms():
    """Active meters the nav cannot place: in no active group and not filed by hand."""
    return (MFM.objects.filter(active=True).exclude(_IN_ACTIVE_GROUP)
            .exclude(_HAND_FILED))


def _category_node(category, children):
    return {
        'id': f'eq-{category.code}',
        'label': category.label,
        'slug': category.slug,
        'kind': 'section',
        'category': category.code,
        'sort_order': category.sort_order,
        'pathOverride': f'{_MODULE}/{category.slug}',
        'model_slug': None,
        'children': children,
    }


def build_tree():
    """``{count, leaf_count, matched_mfm_count, ungrouped_mfm_count,
    uncategorised_panel_count, uncategorised_mfm_count, tree}``."""
    # PIN 2026-09-09: query budget = 6 flat queries regardless of plant size
    # (categories, groups+config, twins prefetch, memberships+mfm+config,
    # standalone meters, ungrouped count); the tree is bucketed in Python,
    # never per node.
    #
    # NOTE the deliberate absence of .filter(active=True): _category_index needs
    # retired ancestors. Only ACTIVE groups are ever emitted.
    categories = Categories()
    groups = list(Group.objects
                  .select_related('config', 'equipment__config')
                  .prefetch_related('twins')
                  .order_by('sort_order', 'name', 'id'))
    by_id = {g.id: g for g in groups}
    cat_by_group = _category_index(groups, categories)

    members_by_group, claimed = {}, set()
    for m in (Membership.objects
              .filter(group__active=True, mfm__active=True)
              .select_related('mfm', 'mfm__config', 'mfm__breaker')
              .order_by('position', 'id')):
        members_by_group.setdefault(m.group_id, []).append(m)
        g = by_id.get(m.group_id)
        if g is not None and g.is_panel:
            claimed.add(m.mfm_id)      # a panel member NEVER also floats loose

    stats = {'leaves': 0, 'matched': set(), 'uncat_panels': 0, 'uncat_mfms': 0}
    buckets = {c.code: [] for c in categories.rows}

    # --- panels, in canonical group order ----------------------------------
    for g in groups:
        if not g.active or not g.is_panel:
            continue
        cid = cat_by_group.get(g.id)
        if cid is None:
            stats['uncat_panels'] += 1
            continue                   # not placeable yet; see the import census
        buckets[cid].append(
            _panel_node(g, members_by_group.get(g.id, ()), cid, by_id, stats))

    # --- loose leaves: ONE per meter tree-wide, claimed meters skipped ------
    # insertion order = canonical group order, then the logger's devices[] index
    first_row, plain_groups = {}, {}
    for g in groups:
        if not g.active or g.is_panel:
            continue
        for m in members_by_group.get(g.id, ()):
            plain_groups.setdefault(m.mfm_id, []).append(g.id)
            first_row.setdefault(m.mfm_id, m)
    for mfm_id, gids in plain_groups.items():
        if mfm_id in claimed:
            continue
        mfm = first_row[mfm_id].mfm
        cid = (categories.known(mfm.nav_category)
               or next((c for c in (cat_by_group.get(x) for x in gids) if c), None))
        if cid is None:
            stats['uncat_mfms'] += 1
            continue
        buckets[cid].append(_loose_leaf(mfm, cid, stats))

    # --- standalone meters: in no active group, filed by hand ---------------
    # No active membership means no panel claims them and no plain group
    # emitted them.
    for mfm in (standalone_mfms().select_related('config', 'breaker')
                .order_by('name', 'id')):
        if mfm.nav_category not in buckets:   # a hidden or stale code; never guess
            stats['uncat_mfms'] += 1
            continue
        buckets[mfm.nav_category].append(_loose_leaf(mfm, mfm.nav_category, stats))

    tree = [_category_node(c, buckets[c.code]) for c in categories.rows
            if buckets[c.code] or not c.hide_when_empty]
    ungrouped = ungrouped_mfms().count()
    return {
        'count': len(tree),
        'leaf_count': stats['leaves'],
        'matched_mfm_count': len(stats['matched']),
        'ungrouped_mfm_count': ungrouped,
        'uncategorised_panel_count': stats['uncat_panels'],
        'uncategorised_mfm_count': stats['uncat_mfms'],
        'tree': tree,
    }
