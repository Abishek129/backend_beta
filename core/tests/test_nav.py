"""Nav tree over the GROUPS_AND_MFMS.md §8 worked example, built with the ORM.

    Plant                                     (no category)
    ├── Yard                 nav_category=hvac       MFM-09 (loose, no role)
    └── Block A              nav_category=production
        ├── MCC-1   panel  panel_kind=pcc_panel  twins MCC-2
        │                    MFM-01 in, MFM-02 out, MFM-03 out
        ├── MCC-2   panel  (no config)           -> INHERITS production
        │                    MFM-05 in, MFM-06 out
        └── Lighting DB panel  nav_category=utility (own value wins)
                             MFM-03 in (same device), MFM-04 out

So the fixture exercises every placement path: the panel_kind axis, an own
nav_category, inheritance from an ancestor, and a loose meter inheriting from
its plain group.
"""
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from core.constants import FEEDER_ID_OFFSET
from core.models import (DataSource, Group, Membership, MFM, MfmConfig, NavCategory,
                         PanelConfig)
from core.nav import build_tree
from core.nav_categories import NAV_CATEGORIES


def _by_slug(nodes, slug):
    return next(n for n in nodes if n['slug'] == slug)


def _walk(nodes):
    stack = list(nodes)
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.get('children') or [])


class NavTreeTests(TestCase):
    def setUp(self):
        self.ds = DataSource.objects.create(name='logger', dsn='postgresql://x/y')

        self.plant = Group.objects.create(logger_id='grp_plant', name='Plant',
                                          slug='plant', sort_order=0)
        self.yard = Group.objects.create(logger_id='grp_yard', name='Yard', slug='yard',
                                         parent=self.plant, sort_order=0,
                                         nav_category='hvac')
        self.block = Group.objects.create(logger_id='grp_blockA', name='Block A',
                                          slug='block-a', parent=self.plant, sort_order=1,
                                          nav_category='production')
        self.mcc1 = Group.objects.create(logger_id='grp_mcc1', name='MCC-1', slug='mcc-1',
                                         parent=self.block, sort_order=0, is_panel=True)
        self.mcc2 = Group.objects.create(logger_id='grp_mcc2', name='MCC-2', slug='mcc-2',
                                         parent=self.block, sort_order=1, is_panel=True)
        self.ldb = Group.objects.create(logger_id='grp_ldb', name='Lighting DB',
                                        slug='lighting-db', parent=self.block,
                                        sort_order=2, is_panel=True,
                                        nav_category='utility')
        self.mcc1.twins.add(self.mcc2)
        # a retired section must not appear at all
        self.old = Group.objects.create(logger_id='grp_old', name='Old Shed', slug='old-shed',
                                        parent=self.plant, sort_order=9, active=False,
                                        retired_at=timezone.now(), nav_category='utility')
        PanelConfig.objects.create(group=self.mcc1, nominal_voltage_v=11000.0,
                                   panel_kind='pcc_panel')

        self.mfm = {}
        for n in (1, 2, 3, 4, 5, 6, 9, 10):
            self.mfm[n] = MFM.objects.create(logger_id=f'dev_{n:02d}', name=f'MFM-{n:02d}',
                                             slug=f'mfm-{n:02d}')
        # bound = has BOTH a data source and a table name
        for n in (1, 3, 9, 10):
            MFM.objects.filter(pk=self.mfm[n].pk).update(data_source=self.ds,
                                                         table_name=f't_mfm_{n:02d}',
                                                         table_binding='auto')
            self.mfm[n].refresh_from_db()
        MfmConfig.objects.create(mfm=self.mfm[1], device_type='transformer',
                                 nominal_voltage_v=11000.0)
        # retired meter with a live membership — excluded from the tree
        self.retired_mfm = MFM.objects.create(logger_id='dev_99', name='MFM-99', slug='mfm-99',
                                              active=False, retired_at=timezone.now())
        Membership.objects.create(group=self.yard, mfm=self.retired_mfm, position=1)

        Membership.objects.create(group=self.yard, mfm=self.mfm[9], position=0)
        for pos, (n, role) in enumerate([(1, 'incoming'), (2, 'outgoing'), (3, 'outgoing')]):
            Membership.objects.create(group=self.mcc1, mfm=self.mfm[n], role=role, position=pos)
        for pos, (n, role) in enumerate([(5, 'incoming'), (6, 'outgoing')]):
            Membership.objects.create(group=self.mcc2, mfm=self.mfm[n], role=role, position=pos)
        for pos, (n, role) in enumerate([(3, 'incoming'), (4, 'outgoing')]):
            Membership.objects.create(group=self.ldb, mfm=self.mfm[n], role=role, position=pos)
        # MFM-10 is bound but in no group -> ungrouped, never in the tree

        self.data = build_tree()
        self.tree = self.data['tree']

    # --- the fixed category spine --------------------------------------------
    def test_visible_categories_keep_canonical_order(self):
        self.assertEqual([n['slug'] for n in self.tree],
                         [slug for cid, _label, slug in NAV_CATEGORIES if cid != 'apfc'])
        self.assertEqual([n['label'] for n in self.tree],
                         [label for cid, label, _slug in NAV_CATEGORIES if cid != 'apfc'])
        self.assertTrue(all(n['kind'] == 'section' for n in self.tree))

    def test_an_empty_category_is_still_emitted(self):
        for slug in ('ht-panels', 'amf-panels', 'ups-panels'):
            with self.subTest(slug=slug):
                self.assertEqual(_by_slug(self.tree, slug)['children'], [])

    def test_empty_apfc_and_hvac_are_hidden(self):
        MFM.objects.filter(pk=self.mfm[9].pk).update(active=False)
        data = build_tree()
        self.assertFalse({'apfc-panels', 'hvac-panels'} &
                         {node['slug'] for node in data['tree']})
        self.assertEqual(data['count'], len(data['tree']))

    def test_populated_apfc_is_not_hidden(self):
        Group.objects.create(name='APFC Test', slug='apfc-test',
                             is_panel=True, nav_category='apfc')
        apfc = _by_slug(build_tree()['tree'], 'apfc-panels')
        self.assertEqual(len(apfc['children']), 1)

    def test_category_node_shape(self):
        pcc = _by_slug(self.tree, 'pcc-panels')
        self.assertEqual(pcc['id'], 'eq-pcc')
        self.assertEqual(pcc['category'], 'pcc')
        self.assertEqual(pcc['sort_order'], 2)
        self.assertEqual(pcc['pathOverride'], '/electrical/pcc-panels')
        self.assertIsNone(pcc['model_slug'])

    def test_the_tree_is_exactly_two_container_levels(self):
        for category in self.tree:
            for node in category['children']:
                with self.subTest(node=node['slug']):
                    self.assertIn(node['kind'], ('panel',))
                    # a panel's children are Overview + role groups only —
                    # never another section.
                    for child in node.get('children') or []:
                        self.assertNotEqual(child.get('kind'), 'section')

    # --- placement -----------------------------------------------------------
    def test_a_panel_is_placed_by_its_panel_kind(self):
        self.assertEqual([n['slug'] for n in _by_slug(self.tree, 'pcc-panels')['children']],
                         ['mcc-1'])
        self.assertEqual(self._panel('mcc-1')['category'], 'pcc')

    def test_a_panel_inherits_its_category_from_an_ancestor(self):
        # MCC-2 has no PanelConfig and no nav_category — Block A supplies it.
        self.assertEqual([n['slug'] for n in
                          _by_slug(self.tree, 'production-panels')['children']],
                         ['mcc-2'])

    def test_an_own_nav_category_beats_the_inherited_one(self):
        # Lighting DB sits under Block A (production) but declares utility.
        self.assertEqual([n['slug'] for n in
                          _by_slug(self.tree, 'utility-panels')['children']],
                         ['lighting-db'])

    def test_a_loose_meter_inherits_from_its_plain_group(self):
        hvac = _by_slug(self.tree, 'hvac-panels')['children']
        self.assertEqual([n['label'] for n in hvac], ['MFM-09'])   # MFM-99 retired
        self.assertEqual(hvac[0]['category'], 'hvac')

    def test_a_panel_carries_its_nearest_active_ancestor_as_parent_label(self):
        self.assertEqual(self._panel('mcc-1')['parent_label'], 'Block A')

    def test_a_retired_ancestor_still_donates_its_category(self):
        """Soft-retiring a section must never re-bucket the panels under it."""
        Group.objects.filter(pk=self.block.pk).update(active=False)
        tree = build_tree()['tree']
        self.assertEqual([n['slug'] for n in _by_slug(tree, 'production-panels')['children']],
                         ['mcc-2'])

    def test_an_uncategorised_panel_is_counted_and_not_rendered(self):
        orphan = Group.objects.create(logger_id='grp_x', name='Mystery Board',
                                      slug='mystery-board', is_panel=True)
        data = build_tree()
        self.assertEqual(data['uncategorised_panel_count'], 1)
        self.assertNotIn(orphan.slug, [n['slug'] for n in _walk(data['tree'])])

    # --- panel / leaf shapes (unchanged from the arbitrary-depth tree) --------
    def test_panel_node_shape(self):
        mcc1 = self._panel('mcc-1')
        self.assertEqual(mcc1['id'], f'grp-{self.mcc1.id}')      # Group.id, not logger_id
        self.assertEqual(mcc1['logger_id'], 'grp_mcc1')
        self.assertEqual(mcc1['key'], 'mcc-1')
        self.assertEqual(mcc1['kind'], 'panel')
        self.assertTrue(mcc1['distribution_panel'])
        self.assertEqual(mcc1['mfm_name'], 'MCC-1')
        self.assertEqual(mcc1['twins'], ['mcc-2'])
        self.assertIsNone(mcc1['model_slug'])
        self.assertEqual(mcc1['bus_kv'], 11.0)                  # PanelConfig L-L verbatim
        self.assertEqual(self._panel('mcc-2')['bus_kv'], 0.41)  # 415 V default

    def test_role_groups_present_and_empty_ones_omitted(self):
        mcc1 = self._panel('mcc-1')
        self.assertEqual([n['slug'] for n in mcc1['children']],
                         ['overview', 'incoming', 'outgoing'])   # no Spare group
        overview = mcc1['children'][0]
        self.assertEqual(overview, {'id': f'grp-{self.mcc1.id}-overview',
                                    'label': 'Overview', 'slug': 'overview'})
        group_node = _by_slug(mcc1['children'], 'outgoing')
        self.assertEqual(group_node['id'], f'grp-{self.mcc1.id}-outgoing')
        self.assertEqual(group_node['label'], 'Outgoing')
        self.assertTrue(group_node['group'])
        self.assertTrue(group_node['alwaysOpen'])

    def test_overview_is_emitted_for_every_panel(self):
        for slug in ('mcc-1', 'mcc-2', 'lighting-db'):
            with self.subTest(slug=slug):
                self.assertEqual(self._panel(slug)['children'][0]['slug'], 'overview')

    # --- ids -----------------------------------------------------------------
    def test_panel_id_is_the_group_id_and_leaf_id_is_offset(self):
        self.assertEqual(self._panel('mcc-1')['mfm_id'], self.mcc1.id)
        self.assertLess(self._panel('mcc-1')['mfm_id'], FEEDER_ID_OFFSET)
        leaf = self._leaf('mcc-1', 'incoming', 'MFM-01')
        self.assertEqual(leaf['mfm_id'], FEEDER_ID_OFFSET + self.mfm[1].id)
        self.assertEqual(leaf['id'], f"grp-{self.mcc1.id}-mfm-{FEEDER_ID_OFFSET + self.mfm[1].id}")

    def test_member_leaf_shape(self):
        leaf = self._leaf('mcc-1', 'incoming', 'MFM-01')
        self.assertEqual(leaf['key'], 'mfm-01')
        self.assertEqual(leaf['slug'], 'mfm-01')
        self.assertEqual(leaf['role'], 'incoming')
        self.assertEqual(leaf['logger_device_id'], 'dev_01')
        self.assertIsNone(leaf['mfm_row'])
        self.assertIsNone(leaf['breaker_type'])
        self.assertFalse(leaf['distribution_panel'])
        self.assertEqual(leaf['nominal_kv'], 11.0)              # MfmConfig L-L, raw
        self.assertEqual(self._leaf('mcc-1', 'outgoing', 'MFM-02')['nominal_kv'], 0.41)

    def test_a_meter_in_two_panels_keeps_one_mfm_id_and_two_node_ids(self):
        a = self._leaf('mcc-1', 'outgoing', 'MFM-03')
        b = self._leaf('lighting-db', 'incoming', 'MFM-03')
        self.assertEqual(a['mfm_id'], b['mfm_id'])
        self.assertEqual(a['mfm_id'], FEEDER_ID_OFFSET + self.mfm[3].id)
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(a['role'], 'outgoing')
        self.assertEqual(b['role'], 'incoming')

    def test_node_ids_are_unique_tree_wide(self):
        ids = [n['id'] for n in _walk(self.tree)]
        self.assertEqual(len(ids), len(set(ids)))

    # --- loose leaves, retired rows, counts ----------------------------------
    def test_loose_meter_renders_as_the_flat_leaf(self):
        leaf = _by_slug(self.tree, 'hvac-panels')['children'][0]
        eid = FEEDER_ID_OFFSET + self.mfm[9].id
        self.assertEqual(leaf['id'], f'eq-mfm-{eid}')
        self.assertEqual(leaf['mfm_id'], eid)
        self.assertEqual(leaf['mfm_ids'], [eid])
        self.assertEqual(leaf['kind'], 'panel')
        self.assertFalse(leaf['distribution_panel'])
        self.assertEqual(leaf['key'], 'mfm-09')
        self.assertEqual(leaf['logger_device_id'], 'dev_09')
        self.assertEqual(leaf['bus_kv'], 0.41)

    def test_a_meter_a_panel_claims_never_also_floats_loose(self):
        """MFM-02 is an MCC-1 outgoer; adding it to a plain group too must not
        make it appear a second time as a standalone node."""
        Membership.objects.create(group=self.yard, mfm=self.mfm[2], position=5)
        data = build_tree()
        eid = FEEDER_ID_OFFSET + self.mfm[2].id
        self.assertNotIn(f'eq-mfm-{eid}', [n['id'] for n in _walk(data['tree'])])

    def test_a_meter_in_two_plain_groups_yields_exactly_one_loose_leaf(self):
        """Regression: the recursive tree emitted one loose leaf per MEMBERSHIP,
        so a meter in two plain groups produced two nodes sharing an id."""
        other = Group.objects.create(logger_id='grp_shed', name='Shed', slug='shed',
                                     parent=self.plant, sort_order=3, nav_category='hvac')
        Membership.objects.create(group=other, mfm=self.mfm[9], position=0)
        data = build_tree()
        eid = FEEDER_ID_OFFSET + self.mfm[9].id
        ids = [n['id'] for n in _walk(data['tree'])]
        self.assertEqual(ids.count(f'eq-mfm-{eid}'), 1)
        self.assertEqual(len(ids), len(set(ids)))

    def test_retired_group_is_excluded(self):
        self.assertNotIn('old-shed', [n['slug'] for n in _walk(self.tree)])

    def test_envelope_counts(self):
        self.assertEqual(self.data['count'], 7)                 # empty APFC hidden
        # 3 (MCC-1) + 2 (MCC-2) + 2 (Lighting DB) + 1 loose (MFM-09)
        self.assertEqual(self.data['leaf_count'], 8)
        # bound AND in the tree: MFM-01, MFM-03, MFM-09 (MFM-10 is bound but ungrouped)
        self.assertEqual(self.data['matched_mfm_count'], 3)
        self.assertEqual(self.data['ungrouped_mfm_count'], 1)   # MFM-10
        self.assertEqual(self.data['uncategorised_panel_count'], 0)
        self.assertEqual(self.data['uncategorised_mfm_count'], 0)

    # --- views ---------------------------------------------------------------
    def test_ems_view(self):
        r = self.client.get('/api/ems/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), {'count', 'leaf_count', 'matched_mfm_count',
                                     'ungrouped_mfm_count', 'uncategorised_panel_count',
                                     'uncategorised_mfm_count', 'tree'})
        self.assertEqual(body['tree'][0]['slug'], 'ht-panels')

    def test_mfm_config_view_for_panel_and_meter(self):
        r = self.client.get(f'/api/mfm/{self.mcc1.id}/config/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), {'mfm_id', 'panel_id', 'mfm_type',
                                     'config_table', 'config', 'object'})
        self.assertEqual(body['panel_id'], 'mcc-1')
        self.assertEqual(body['mfm_type'], 'pcc_panel')
        self.assertEqual(body['config_table'], 'panel_config')
        self.assertEqual(body['config']['voltage_nominal_v'], 11000.0)
        self.assertIsNone(body['object'])

        eid = FEEDER_ID_OFFSET + self.mfm[1].id
        body = self.client.get(f'/api/mfm/{eid}/config/').json()
        self.assertEqual(body['config_table'], 'mfm_config')
        self.assertEqual(body['mfm_type'], 'transformer')
        self.assertEqual(body['panel_id'], 'mfm-01')

    def test_mfm_config_view_404(self):
        r = self.client.get('/api/mfm/987654/config/')
        self.assertEqual(r.status_code, 404)
        self.assertIn('detail', r.json())

    def test_status_view(self):
        r = self.client.get('/api/status/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body), {'db', 'logger_api_reachable', 'groups', 'panels',
                                     'mfms', 'bound_mfms', 'ungrouped_mfms',
                                     'last_import_at', 'version'})
        self.assertTrue(body['db'])
        self.assertEqual(body['groups'], 6)      # Old Shed is retired
        self.assertEqual(body['panels'], 3)
        self.assertEqual(body['mfms'], 8)        # MFM-99 is retired
        self.assertEqual(body['bound_mfms'], 4)
        self.assertEqual(body['ungrouped_mfms'], 1)
        self.assertIsInstance(body['logger_api_reachable'], bool)
        self.assertEqual(body['version'], 'beta')

    # --- budget --------------------------------------------------------------
    def test_ems_query_budget(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get('/api/ems/')
        self.assertLessEqual(len(ctx.captured_queries), 7)

    # --- helpers -------------------------------------------------------------
    def _panel(self, slug):
        return next(n for n in _walk(self.tree)
                    if n.get('kind') == 'panel' and n['slug'] == slug)

    def _leaf(self, panel_slug, role, label):
        group_node = _by_slug(self._panel(panel_slug)['children'], role)
        return next(n for n in group_node['children'] if n['label'] == label)


class CategoryEdgeCaseTests(TestCase):
    """Placement corners the worked example does not reach."""

    def test_a_panel_kind_beats_an_inherited_nav_category(self):
        root = Group.objects.create(logger_id='grp_r', name='Plant Room', slug='plant-room',
                                    nav_category='hvac')
        panel = Group.objects.create(logger_id='grp_p', name='PCC', slug='pcc',
                                     parent=root, is_panel=True)
        PanelConfig.objects.create(group=panel, panel_kind='pcc_panel')
        tree = build_tree()['tree']
        self.assertEqual([n['slug'] for n in _by_slug(tree, 'pcc-panels')['children']],
                         ['pcc'])
        self.assertNotIn('hvac-panels', [node['slug'] for node in tree])

    def test_the_default_panel_kind_falls_through_to_nav_category(self):
        panel = Group.objects.create(logger_id='grp_p', name='Board', slug='board',
                                     is_panel=True, nav_category='apfc')
        PanelConfig.objects.create(group=panel, panel_kind='distribution_panel')
        tree = build_tree()['tree']
        self.assertEqual([n['slug'] for n in _by_slug(tree, 'apfc-panels')['children']],
                         ['board'])

    def test_a_nested_panel_becomes_a_sibling_under_its_own_category(self):
        outer = Group.objects.create(logger_id='grp_o', name='Outer', slug='outer',
                                     is_panel=True, nav_category='utility')
        Group.objects.create(logger_id='grp_i', name='Inner', slug='inner',
                             parent=outer, is_panel=True, nav_category='hvac')
        tree = build_tree()['tree']
        self.assertEqual([n['slug'] for n in _by_slug(tree, 'utility-panels')['children']],
                         ['outer'])
        self.assertEqual([n['slug'] for n in _by_slug(tree, 'hvac-panels')['children']],
                         ['inner'])
        # the inner panel is NOT also a child of the outer one
        self.assertNotIn('inner', [c['slug'] for c in
                                   _by_slug(tree, 'utility-panels')['children'][0]['children']])

    def test_a_parent_cycle_does_not_hang_the_walk(self):
        a = Group.objects.create(logger_id='grp_a', name='A', slug='a')
        b = Group.objects.create(logger_id='grp_b', name='B', slug='b', parent=a,
                                 is_panel=True)
        Group.objects.filter(pk=a.pk).update(parent=b)
        data = build_tree()
        self.assertEqual(data['count'], len(data['tree']))
        self.assertEqual(data['uncategorised_panel_count'], 1)

    def test_an_mfm_override_beats_its_groups_category(self):
        group = Group.objects.create(logger_id='grp_g', name='Yard', slug='yard',
                                     nav_category='hvac')
        mfm = MFM.objects.create(logger_id='dev_a', name='A', slug='a',
                                 nav_category='production')
        Membership.objects.create(group=group, mfm=mfm, position=0)
        tree = build_tree()['tree']
        self.assertEqual([n['label'] for n in
                          _by_slug(tree, 'production-panels')['children']], ['A'])
        self.assertNotIn('hvac-panels', [node['slug'] for node in tree])

    def test_an_uncategorised_loose_meter_is_counted_and_not_rendered(self):
        group = Group.objects.create(logger_id='grp_g', name='Yard', slug='yard')
        mfm = MFM.objects.create(logger_id='dev_a', name='A', slug='a')
        Membership.objects.create(group=group, mfm=mfm, position=0)
        data = build_tree()
        self.assertEqual(data['uncategorised_mfm_count'], 1)
        self.assertEqual(data['leaf_count'], 0)
        self.assertNotIn('a', [n['slug'] for n in _walk(data['tree'])])


class UserCategoryTests(TestCase):
    """The categories are NavCategory rows, not a fixed list."""

    def test_a_hand_set_category_beats_the_panel_kind_but_a_guess_does_not(self):
        hand = Group.objects.create(name='Hand', slug='hand', is_panel=True,
                                    nav_category='utility', nav_category_source='manual')
        guess = Group.objects.create(logger_id='grp_g', name='Guess', slug='guess',
                                     is_panel=True, nav_category='utility')
        for g in (hand, guess):
            PanelConfig.objects.create(group=g, panel_kind='pcc_panel')
        tree = build_tree()['tree']
        self.assertEqual([n['slug'] for n in _by_slug(tree, 'utility-panels')['children']],
                         ['hand'])
        self.assertEqual([n['slug'] for n in _by_slug(tree, 'pcc-panels')['children']],
                         ['guess'])

    def test_a_new_category_renders_in_its_sort_position(self):
        NavCategory.objects.create(code='solar', label='Solar', slug='solar', sort_order=1)
        Group.objects.create(name='Array 1', slug='array-1', is_panel=True,
                             nav_category='solar', nav_category_source='manual')
        tree = build_tree()['tree']
        self.assertEqual([n['category'] for n in tree][:3], ['ht', 'amf', 'solar'])
        self.assertEqual(_by_slug(tree, 'solar')['pathOverride'], '/electrical/solar')

    def test_a_hidden_category_is_omitted_and_its_rows_counted(self):
        NavCategory.objects.filter(code='utility').update(active=False)
        Group.objects.create(name='Board', slug='board', is_panel=True,
                             nav_category='utility', nav_category_source='manual')
        data = build_tree()
        self.assertNotIn('utility', [n['category'] for n in data['tree']])
        self.assertEqual(data['uncategorised_panel_count'], 1)

    def test_a_kind_places_through_the_first_category_defaulting_to_it(self):
        NavCategory.objects.filter(code='pcc').update(active=False)
        NavCategory.objects.create(code='mcc', label='MCC', slug='mcc', sort_order=2,
                                   panel_kind='pcc_panel')
        panel = Group.objects.create(logger_id='grp_p', name='PCC-9', slug='pcc-9',
                                     is_panel=True)
        PanelConfig.objects.create(group=panel, panel_kind='pcc_panel')
        self.assertEqual([n['slug'] for n in _by_slug(build_tree()['tree'], 'mcc')['children']],
                         ['pcc-9'])

    def test_a_hand_filed_meter_whose_groups_all_retired_shows_standalone(self):
        old = Group.objects.create(name='Old', slug='old', is_panel=True, active=False)
        mfm = MFM.objects.create(name='M', slug='m', nav_category='hvac',
                                 nav_category_source='manual')
        Membership.objects.create(group=old, mfm=mfm, role='incoming')
        tree = build_tree()['tree']
        self.assertEqual([n['label'] for n in _by_slug(tree, 'hvac-panels')['children']], ['M'])
