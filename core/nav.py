"""EMS nav tree — a FIXED two-level sidebar.

    category (core.nav_categories.NAV_CATEGORIES, stable order, always emitted)
      ├── panel      (Group.is_panel) -> Overview + Incoming/Outgoing/Spare
      └── loose leaf (an active MFM that no panel claims)

Intermediate non-panel Groups no longer render. They keep two jobs: they own
memberships (their meters become loose leaves) and they carry ``nav_category``
for their whole subtree.

There is NO per-category mode — backend2's ``mode: hierarchical|flat`` is gone.
Whether a node expands is derived per node from ``Group.is_panel``, so a
distribution panel gets its role groups wherever it is filed.

Two orthogonal axes place a panel (see core.nav_categories):
``PanelConfig.panel_kind`` -> HT/AMF/PCC/UPS, ``nav_category`` -> the rest.
Node shapes reuse the keys CMD_V2 already reads; see ``NAV_CONTRACT.md``.
"""
from core.config import resolve_panel_config
from core.config_defaults import defaults_for
from core.constants import FEEDER_ID_OFFSET
from core.models import Group, Membership, MFM
from core.nav_categories import NAV_CATEGORIES, PANEL_KIND_CATEGORY

# FE module basePath the category slugs hang under (pathOverride).
_MODULE = '/electrical'
# Role groups, in display order; empty ones are omitted.
_ROLES = [('incoming', 'Incoming'), ('outgoing', 'Outgoing'), ('spare', 'Spare')]


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


def _group_category(group):
    """A group's OWN category: panel_kind first, then nav_category.

    ``panel_kind`` only means anything on a panel — a plain group has no
    PanelConfig — and its 'distribution_panel' default maps to nothing, so such
    a row falls through to ``nav_category`` and then to its ancestors.
    """
    if group.is_panel:
        cfg = getattr(group, 'config', None)
        cid = PANEL_KIND_CATEGORY.get(getattr(cfg, 'panel_kind', '') or '')
        if cid:
            return cid
    return group.nav_category or None


def _category_index(groups):
    """``{Group.id: category id or None}`` — own category, else the nearest
    ancestor that has one.

    ``groups`` MUST include retired rows. nav has always guaranteed that
    soft-retiring a section never hides the panels under it; if the walk
    stopped at an inactive parent, retiring one section would silently
    re-bucket its whole subtree. ``_retire_vanished`` preserves ``parent_id``,
    so the chain is intact. Memoised: O(n), no queries, cycle-safe.
    """
    own = {g.id: _group_category(g) for g in groups}
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
        'mfm_row': None,
        'breaker_type': None,
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
    panel_id = f'grp-{group.logger_id}'
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


def _category_node(cid, label, slug, index, children):
    return {
        'id': f'eq-{cid}',
        'label': label,
        'slug': slug,
        'kind': 'section',
        'category': cid,
        'sort_order': index,
        'pathOverride': f'{_MODULE}/{slug}',
        'model_slug': None,
        'children': children,
    }


def build_tree():
    """``{count, leaf_count, matched_mfm_count, ungrouped_mfm_count,
    uncategorised_panel_count, uncategorised_mfm_count, tree}``."""
    # PIN 2026-09-09: query budget = 4 flat queries regardless of plant size
    # (groups+config, twins prefetch, memberships+mfm+config, ungrouped count);
    # the tree is bucketed in Python, never per node.
    #
    # NOTE the deliberate absence of .filter(active=True): _category_index needs
    # retired ancestors. Only ACTIVE groups are ever emitted.
    groups = list(Group.objects
                  .select_related('config')
                  .prefetch_related('twins')
                  .order_by('sort_order', 'name', 'id'))
    by_id = {g.id: g for g in groups}
    cat_by_group = _category_index(groups)

    members_by_group, claimed = {}, set()
    for m in (Membership.objects
              .filter(group__active=True, mfm__active=True)
              .select_related('mfm', 'mfm__config')
              .order_by('position', 'id')):
        members_by_group.setdefault(m.group_id, []).append(m)
        g = by_id.get(m.group_id)
        if g is not None and g.is_panel:
            claimed.add(m.mfm_id)      # a panel member NEVER also floats loose

    stats = {'leaves': 0, 'matched': set(), 'uncat_panels': 0, 'uncat_mfms': 0}
    buckets = {cid: [] for cid, _label, _slug in NAV_CATEGORIES}

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
        cid = (mfm.nav_category
               or next((c for c in (cat_by_group.get(x) for x in gids) if c), None))
        if cid is None or cid == '':
            stats['uncat_mfms'] += 1
            continue
        buckets[cid].append(_loose_leaf(mfm, cid, stats))

    tree = [_category_node(cid, label, slug, i, buckets[cid])
            for i, (cid, label, slug) in enumerate(NAV_CATEGORIES)]
    ungrouped = MFM.objects.filter(active=True, memberships__isnull=True).count()
    return {
        'count': len(tree),
        'leaf_count': stats['leaves'],
        'matched_mfm_count': len(stats['matched']),
        'ungrouped_mfm_count': ungrouped,
        'uncategorised_panel_count': stats['uncat_panels'],
        'uncategorised_mfm_count': stats['uncat_mfms'],
        'tree': tree,
    }
