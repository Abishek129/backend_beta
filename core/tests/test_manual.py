"""Panels and meters built by hand in the admin.

Three promises: the admin can file a distribution panel (with its incomers,
outgoers and spares) or a lone MFM under any of the eight categories; the nav
renders them; and the importer never updates, retires or strips such rows.
"""
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from core.constants import FEEDER_ID_OFFSET
from core.importer import manual_slugs
from core.models import DataSource, Group, Membership, MFM, PanelConfig
from core.nav import build_tree
from core.tests.test_importer import D1, FakeLoggerClient, ImporterCase

ROLE_PREFIX = {'incoming': 'memberships', 'outgoing': 'memberships-2', 'spare': 'memberships-3'}


def _cat(tree, cid):
    return next(n for n in tree if n['category'] == cid)


def _management(data, prefix, total, initial=0):
    data.update({f'{prefix}-TOTAL_FORMS': total, f'{prefix}-INITIAL_FORMS': initial,
                 f'{prefix}-MIN_NUM_FORMS': 0, f'{prefix}-MAX_NUM_FORMS': 1000})


# --- nav -----------------------------------------------------------------------
class ManualNavTests(TestCase):
    def test_a_hand_built_panel_is_keyed_on_its_group_id(self):
        panel = Group.objects.create(name='Board A', slug='board-a', is_panel=True,
                                     nav_category='hvac', nav_category_source='manual')
        node = _cat(build_tree()['tree'], 'hvac')['children'][0]
        self.assertEqual(node['id'], f'grp-{panel.id}')
        self.assertEqual(node['mfm_id'], panel.id)
        self.assertIsNone(node['logger_id'])

    def test_a_meter_filed_by_hand_shows_straight_under_any_category(self):
        mfm = MFM.objects.create(name='HT Incomer', slug='ht-incomer',
                                 nav_category='ht', nav_category_source='manual')
        data = build_tree()
        leaf = _cat(data['tree'], 'ht')['children'][0]
        self.assertEqual(leaf['mfm_id'], FEEDER_ID_OFFSET + mfm.id)
        self.assertEqual(leaf['kind'], 'panel')
        self.assertFalse(leaf['distribution_panel'])
        self.assertEqual(data['ungrouped_mfm_count'], 0)

    def test_an_importer_guess_alone_never_files_an_ungrouped_meter(self):
        MFM.objects.create(logger_id='dev_x', name='AHU-3', slug='ahu-3',
                           nav_category='hvac', nav_category_source='auto')
        data = build_tree()
        self.assertEqual(_cat(data['tree'], 'hvac')['children'], [])
        self.assertEqual(data['ungrouped_mfm_count'], 1)

    def test_a_panel_member_is_never_also_a_standalone_leaf(self):
        panel = Group.objects.create(name='Board', slug='board', is_panel=True,
                                     nav_category='utility', nav_category_source='manual')
        mfm = MFM.objects.create(name='M', slug='m', nav_category='hvac',
                                 nav_category_source='manual')
        Membership.objects.create(group=panel, mfm=mfm, role='incoming')
        tree = build_tree()['tree']
        self.assertEqual(_cat(tree, 'hvac')['children'], [])


# --- importer --------------------------------------------------------------------
class ImporterLeavesManualRowsAloneTests(ImporterCase):
    def setUp(self):
        self.run_import()
        self.imported = MFM.objects.get(logger_id=D1)
        self.panel = Group.objects.create(name='Hand Board', slug='hand-board', is_panel=True,
                                          nav_category='hvac', nav_category_source='manual')
        self.meter = MFM.objects.create(name='Hand Meter', slug='hand-meter')
        Membership.objects.create(group=self.panel, mfm=self.imported, role='incoming')
        Membership.objects.create(group=self.panel, mfm=self.meter, role='outgoing')

    def test_a_rerun_keeps_manual_rows_and_their_memberships(self):
        _desired, d, _summary = self.run_import()
        self.assertTrue(Group.objects.get(pk=self.panel.pk).active)
        self.assertTrue(MFM.objects.get(pk=self.meter.pk).active)
        self.assertEqual(MFM.objects.get(pk=self.meter.pk).slug, 'hand-meter')
        self.assertEqual(Membership.objects.filter(group=self.panel).count(), 2)
        retired = [e['key'] for name in ('groups', 'mfms', 'memberships')
                   for e in d.entities[name].retire]
        self.assertEqual(retired, [])

    def test_manual_slugs_are_reserved_for_the_import(self):
        Group.objects.exclude(pk=self.panel.pk).delete()
        MFM.objects.exclude(pk=self.meter.pk).delete()
        Group.objects.filter(pk=self.panel.pk).update(slug='mcc-1')
        self.assertIn('mcc-1', manual_slugs())
        self.run_import(FakeLoggerClient(), reserved_slugs=manual_slugs())
        self.assertEqual(Group.objects.get(pk=self.panel.pk).slug, 'mcc-1')
        imported = Group.objects.exclude(pk=self.panel.pk).get(name='MCC-1')
        self.assertNotEqual(imported.slug, 'mcc-1')


# --- admin -----------------------------------------------------------------------
DEVICES = {
    'dev_a': {'id': 'dev_a', 'name': 'AHU_03', 'protocol': 'modbus', 'gateway_id': 'gw_1',
              'unit_id': 4, 'port': 502, 'status': 'connected', 'table_name': 'gic_ahu_03',
              'table_id': 'tbl_1', 'db_target_id': 'pg_main', 'ambiguous': False},
    'dev_b': {'id': 'dev_b', 'name': 'SPARE_METER', 'protocol': 'modbus', 'gateway_id': 'gw_1',
              'unit_id': 5, 'port': 502, 'status': 'connected', 'table_name': '',
              'table_id': '', 'db_target_id': '', 'ambiguous': False},
}


class AdminCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('admin', 'admin@example.com', 'pw')
        self.client.force_login(self.user)
        patcher = mock.patch('core.logger_devices.devices', return_value=DEVICES)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.m = {n: MFM.objects.create(name=f'MFM-{n}', slug=f'mfm-{n}') for n in (1, 2, 3)}

    def panel_post(self, name, category, rows=None, existing=None):
        """``rows``: {role: [mfm, …]} new rows; ``existing``: {role: [(membership, delete)]}."""
        data = {'name': name, 'category': category, 'slug': '', 'sort_order': 0, 'active': 'on'}
        rows, existing = rows or {}, existing or {}
        for role, prefix in ROLE_PREFIX.items():
            old, new = existing.get(role, []), rows.get(role, [])
            _management(data, prefix, len(old) + len(new), initial=len(old))
            for i, (membership, delete) in enumerate(old):
                data[f'{prefix}-{i}-id'] = membership.pk
                data[f'{prefix}-{i}-mfm'] = membership.mfm_id
                data[f'{prefix}-{i}-position'] = membership.position
                if delete:
                    data[f'{prefix}-{i}-DELETE'] = 'on'
            for j, mfm in enumerate(new, start=len(old)):
                data[f'{prefix}-{j}-mfm'] = mfm.pk
                data[f'{prefix}-{j}-position'] = j
        _management(data, 'config', 0)
        _management(data, 'rtm_thresholds', 0)
        return data


class DistributionPanelAdminTests(AdminCase):
    URL = '/admin/core/distributionpanel/'

    def test_add_a_panel_with_incomers_outgoers_and_spares(self):
        r = self.client.post(self.URL + 'add/', self.panel_post(
            'Board A', 'hvac',
            {'incoming': [self.m[1]], 'outgoing': [self.m[2]], 'spare': [self.m[3]]}))
        self.assertEqual(r.status_code, 302, r.content[:2000])
        panel = Group.objects.get(name='Board A')
        self.assertTrue(panel.is_panel)
        self.assertIsNone(panel.logger_id)
        self.assertEqual(panel.slug, 'board-a')
        self.assertEqual((panel.nav_category, panel.nav_category_source), ('hvac', 'manual'))
        self.assertEqual(PanelConfig.objects.get(group=panel).panel_kind, 'distribution_panel')
        self.assertEqual(dict(Membership.objects.filter(group=panel)
                              .values_list('mfm__name', 'role')),
                         {'MFM-1': 'incoming', 'MFM-2': 'outgoing', 'MFM-3': 'spare'})
        node = _cat(build_tree()['tree'], 'hvac')['children'][0]
        self.assertEqual([c['slug'] for c in node['children']],
                         ['overview', 'incoming', 'outgoing', 'spare'])

    def test_a_structural_category_sets_the_panel_kind(self):
        r = self.client.post(self.URL + 'add/', self.panel_post(
            'PCC-1', 'pcc', {'incoming': [self.m[1]]}))
        self.assertEqual(r.status_code, 302, r.content[:2000])
        panel = Group.objects.get(name='PCC-1')
        self.assertEqual(panel.nav_category, '')
        self.assertEqual(panel.config.panel_kind, 'pcc_panel')
        self.assertEqual(_cat(build_tree()['tree'], 'pcc')['children'][0]['slug'], 'pcc-1')

    def test_one_meter_in_two_roles_is_refused_not_a_500(self):
        r = self.client.post(self.URL + 'add/', self.panel_post(
            'Board B', 'utility', {'incoming': [self.m[1]], 'outgoing': [self.m[1]]}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already listed under another role')
        self.assertFalse(Group.objects.filter(name='Board B').exists())

    def test_a_meter_moves_between_roles_in_one_save(self):
        panel = Group.objects.create(name='Board C', slug='board-c', is_panel=True,
                                     nav_category='utility', nav_category_source='manual')
        PanelConfig.objects.create(group=panel)
        row = Membership.objects.create(group=panel, mfm=self.m[1], role='incoming')
        r = self.client.post(f'{self.URL}{panel.pk}/change/', self.panel_post(
            'Board C', 'utility', rows={'outgoing': [self.m[1]]},
            existing={'incoming': [(row, True)]}))
        self.assertEqual(r.status_code, 302, r.content[:2000])
        self.assertEqual(list(Membership.objects.filter(group=panel).values_list('role', flat=True)),
                         ['outgoing'])

    def test_the_category_link_prefills_the_add_form(self):
        r = self.client.get(self.URL + 'add/?category=apfc')
        self.assertContains(r, '<option value="apfc" selected>')


class MFMAdminTests(AdminCase):
    URL = '/admin/core/mfm/'

    def mfm_post(self, **overrides):
        data = {'logger_id': '', 'name': '', 'slug': '', 'nav_category': '', 'active': 'on',
                'table_name': '', 'data_source': '', 'table_binding': 'none',
                'logger_table_id': '', 'energy_direction': 'sum', 'energy_scale': 1.0,
                'power_scale': 1.0, 'protocol': '', 'gateway_id': '', 'unit_id': '',
                'port': '', 'logger_status': '', 'nav_category_source': 'auto'}
        data.update(overrides)
        _management(data, 'config', 0)
        _management(data, 'thresholds', 0)
        return data

    def test_picking_a_device_fills_the_binding(self):
        r = self.client.post(self.URL + 'add/', self.mfm_post(logger_id='dev_a',
                                                              nav_category='hvac'))
        self.assertEqual(r.status_code, 302, r.content[:2000])
        mfm = MFM.objects.get(logger_id='dev_a')
        self.assertEqual((mfm.name, mfm.slug), ('AHU_03', 'ahu-03'))
        self.assertEqual((mfm.table_name, mfm.logger_table_id, mfm.table_binding),
                         ('gic_ahu_03', 'tbl_1', 'manual'))
        self.assertEqual((mfm.unit_id, mfm.port, mfm.protocol), (4, 502, 'modbus'))
        ds = mfm.data_source
        self.assertEqual((ds.logger_db_target_id, ds.dsn),
                         ('pg_main', settings.LOGGER_TIMESERIES_DSN))
        # filed straight under HVAC, pinned from the start
        self.assertEqual(mfm.nav_category_source, 'manual')
        leaf = _cat(build_tree()['tree'], 'hvac')['children'][0]
        self.assertEqual(leaf['logger_device_id'], 'dev_a')

    def test_an_existing_data_source_is_reused(self):
        ds = DataSource.objects.create(name='logger_pg_main', dsn='postgresql://x/y',
                                       logger_db_target_id='pg_main')
        self.client.post(self.URL + 'add/', self.mfm_post(logger_id='dev_a'))
        self.assertEqual(MFM.objects.get(logger_id='dev_a').data_source, ds)

    def test_a_linked_device_is_not_offered_twice(self):
        MFM.objects.filter(pk=self.m[1].pk).update(logger_id='dev_a')
        r = self.client.get(self.URL + 'add/')
        self.assertNotContains(r, 'value="dev_a"')
        self.assertContains(r, 'value="dev_b"')

    def test_a_name_or_a_device_is_required(self):
        r = self.client.post(self.URL + 'add/', self.mfm_post())
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Give the meter a name, or pick a LoggerFast device.')

    def test_a_slug_taken_by_a_panel_is_refused(self):
        Group.objects.create(name='Board', slug='board', is_panel=True)
        r = self.client.post(self.URL + 'add/', self.mfm_post(name='X', slug='board'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Already used by')


class AdminPagesRenderTests(AdminCase):
    """Every registered admin's list and add page renders — filters, search
    and all. Regression: a filter attribute named ``choices`` shadowed
    ListFilter.choices() and crashed the MFM and Group lists on render."""

    def test_every_changelist_and_add_page_renders(self):
        from django.contrib import admin
        from core.admin import NavCategoryFilter
        Group.objects.create(name='Board', slug='board', is_panel=True, nav_category='hvac')
        request = RequestFactory().get('/')
        request.user = self.user
        for model, model_admin in admin.site._registry.items():
            opts = model._meta
            base = f'/admin/{opts.app_label}/{opts.model_name}/'
            with self.subTest(model=opts.label):
                # RTM opens its default metric via a query-string redirect.
                self.assertEqual(self.client.get(base, follow=True).status_code, 200)
                if any(isinstance(f, type) and issubclass(f, NavCategoryFilter)
                       for f in model_admin.list_filter):
                    self.assertEqual(self.client.get(base + '?nav_category=none').status_code, 200)
                if model_admin.has_add_permission(request):
                    self.assertEqual(self.client.get(base + 'add/').status_code, 200)


class EmsNavPageTests(AdminCase):
    def test_the_page_lists_the_eight_categories_with_add_links(self):
        panel = Group.objects.create(name='Board A', slug='board-a', is_panel=True,
                                     nav_category='hvac', nav_category_source='manual')
        Membership.objects.create(group=panel, mfm=self.m[1], role='incoming')
        r = self.client.get('/admin/core/emsnav/')
        self.assertEqual(r.status_code, 200)
        for label in ('HT Panels', 'AMF Panels', 'PCC Panels', 'UPS Panels', 'APFC Panels',
                      'Production Panels', 'Utility Panels', 'HVAC Panels'):
            self.assertContains(r, label)
        self.assertContains(r, '/admin/core/distributionpanel/add/?category=hvac')
        self.assertContains(r, '/admin/core/mfm/add/?nav_category=ht')
        self.assertContains(r, 'Board A')
        self.assertContains(r, 'Incomers')
