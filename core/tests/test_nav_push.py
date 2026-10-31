"""The JSON nav push behind POST /api/ems/push/ (core.nav_push).

Generic placeholder devices only; LoggerFast is mocked.
"""
import base64
import copy
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.constants import FEEDER_ID_OFFSET
from core.logger_client import LoggerApiError
from core.models import (DataSource, Group, LoggerSchema, Membership, MFM, NavCategory,
                         PanelConfig)


def _device(n):
    return {'id': f'dev_{n}', 'name': f'DEVICE_{n}', 'protocol': 'modbus', 'gateway_id': 'gw_1',
            'unit_id': n, 'port': 502, 'status': 'connected',
            'table_name': f'table_{n:02d}', 'table_id': f'tbl_{n}',
            'db_target_id': 'pg_main', 'ambiguous': False}


DEVICES = {d['id']: d for d in (_device(n) for n in range(1, 9))}

SCHEMA = {
    'code': 'sch_TEST', 'timestamp_column': 'timestamp_utc', 'logger_id': 'sch_100',
    'fields': [
        {'name': 'voltage_avg', 'kind': 'measured', 'data_type': 'float32', 'unit': 'V',
         'address': '40001', 'scale': 0.1},
        {'name': 'power_kw', 'kind': 'derived', 'data_type': 'float32', 'unit': 'kW',
         'formula': 'v * i', 'evaluation_order': 1},
    ],
}


def eid(mfm):
    return FEEDER_ID_OFFSET + mfm.pk


class PushCase(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser('admin', 'admin@example.com', 'pw')
        patcher = mock.patch('core.logger_devices.devices', return_value=DEVICES)
        self.devices = patcher.start()
        self.addCleanup(patcher.stop)

        ds = DataSource.objects.create(name='logger_pg_main', dsn='postgresql://x/y',
                                       logger_db_target_id='pg_main')
        self.m = {}
        for n in range(1, 5):
            self.m[n] = MFM.objects.create(
                name=f'MFM-{n}', slug=f'mfm-{n}', logger_id=f'dev_{n}',
                table_name=f'table_{n:02d}', data_source=ds, table_binding='manual')
        # Panel A: placed by its kind alone, as the importer/admin used to leave it.
        self.a = Group.objects.create(name='Panel A', slug='panel-a', is_panel=True)
        PanelConfig.objects.create(group=self.a, panel_kind='pcc_panel')
        self.old = Group.objects.create(name='Old Board', slug='old-board', is_panel=True,
                                        nav_category='utility', nav_category_source='manual')
        PanelConfig.objects.create(group=self.old)
        Membership.objects.create(group=self.a, mfm=self.m[1], role='incoming')
        Membership.objects.create(group=self.a, mfm=self.m[2], role='outgoing')
        Membership.objects.create(group=self.old, mfm=self.m[3], role='incoming')
        MFM.objects.filter(pk=self.m[4].pk).update(nav_category='production',
                                                   nav_category_source='manual')

    def body(self):
        return {
            'schemas': [SCHEMA],
            'categories': [
                {'code': 'pcc', 'panels': [
                    {'mfm_id': self.a.pk,
                     'incoming': [{'mfm_id': eid(self.m[1])}],
                     'outgoing': [{'mfm_id': eid(self.m[2])}, {'mfm_id': eid(self.m[3])}]}]},
                {'label': 'Solar Panels', 'panels': [
                    {'name': 'Solar PDB', 'nameplate': {'nominal_voltage_v': 415},
                     'incoming': [{'table_name': 'table_05', 'name': 'Solar Incomer',
                                   'schema': 'sch_TEST'}]}]},
            ],
        }

    def push(self, body, commit=False, auth='admin:pw'):
        headers = {}
        if auth:
            headers['HTTP_AUTHORIZATION'] = 'Basic ' + base64.b64encode(auth.encode()).decode()
        url = '/api/ems/push/' + ('?commit=true' if commit else '')
        return self.client.post(url, body, content_type='application/json', **headers)

    def messages(self, response):
        return [f"{e['path']} {e['message']}" for e in response.json()['errors']]


class PreviewAndCommitTests(PushCase):
    def test_a_preview_returns_the_new_nav_and_writes_nothing(self):
        r = self.push(self.body())
        self.assertEqual(r.status_code, 200, r.json())
        data = r.json()
        self.assertFalse(data['committed'])
        self.assertEqual([c['label'] for c in data['nav']['tree']], ['PCC Panels', 'Solar Panels'])
        self.assertFalse(NavCategory.objects.filter(label='Solar Panels').exists())
        self.assertFalse(MFM.objects.filter(logger_id='dev_5').exists())
        self.assertTrue(Group.objects.get(pk=self.old.pk).active)

    def test_commit_makes_the_sidebar_exactly_the_push(self):
        r = self.push(self.body(), commit=True)
        self.assertEqual(r.status_code, 200, r.json())
        self.assertTrue(r.json()['committed'])

        tree = self.client.get('/api/ems/').json()['tree']
        self.assertEqual([(c['category'], c['slug']) for c in tree],
                         [('pcc', 'pcc-panels'), ('solar-panels', 'solar-panels')])
        pcc, solar = tree
        panel = pcc['children'][0]
        self.assertEqual(panel['mfm_id'], self.a.pk)
        roles = {r['slug']: [leaf['label'] for leaf in r['children']]
                 for r in panel['children'] if r.get('group')}
        self.assertEqual(roles, {'incoming': ['MFM-1'], 'outgoing': ['MFM-2', 'MFM-3']})
        self.assertEqual(solar['children'][0]['label'], 'Solar PDB')

        # full sync: the panel, categories and filing the push left out are gone
        self.assertFalse(Group.objects.get(pk=self.old.pk).active)
        self.assertFalse(NavCategory.objects.get(code='production').active)
        self.assertEqual(MFM.objects.get(pk=self.m[4].pk).nav_category, '')
        self.assertTrue(MFM.objects.get(pk=self.m[4].pk).active)        # never retired

    def test_a_new_meter_is_bound_to_its_table_and_schema(self):
        self.push(self.body(), commit=True)
        meter = MFM.objects.get(logger_id='dev_5')
        self.assertEqual((meter.name, meter.table_name, meter.table_binding),
                         ('Solar Incomer', 'table_05', 'manual'))
        self.assertEqual(meter.data_source.logger_db_target_id, 'pg_main')
        self.assertEqual((meter.logger_schema.code, meter.logger_schema.version,
                          meter.logger_schema.timestamp_column), ('sch_TEST', 1, 'timestamp_utc'))
        self.assertEqual(meter.logger_schema.fields.count(), 2)
        solar = Group.objects.get(name='Solar PDB')
        self.assertEqual((solar.config.panel_kind, solar.config.nominal_voltage_v),
                         ('distribution_panel', 415))

    def test_an_existing_panel_keeps_its_kind_and_is_pinned_to_its_category(self):
        self.push(self.body(), commit=True)
        a = Group.objects.get(pk=self.a.pk)
        self.assertEqual((a.nav_category, a.nav_category_source, a.config.panel_kind),
                         ('pcc', 'manual', 'pcc_panel'))

    def test_pushing_the_same_nav_again_changes_nothing_and_needs_no_logger(self):
        self.push(self.body(), commit=True)
        body = self.body()
        meter = MFM.objects.get(logger_id='dev_5')
        body['categories'][1]['panels'][0]['incoming'] = [
            {'mfm_id': eid(meter), 'table_name': 'table_05', 'schema': 'sch_TEST'}]
        self.devices.side_effect = LoggerApiError('down')
        r = self.push(body, commit=True)
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(r.json()['changes'], [])


class CategoryTests(PushCase):
    def test_categories_are_renamed_reordered_and_restyled(self):
        body = {'categories': [
            {'code': 'hvac', 'label': 'Cooling', 'slug': 'cooling', 'hide_when_empty': False},
            {'code': 'ht', 'label': 'High Tension'},
        ]}
        r = self.push(body, commit=True)
        self.assertEqual(r.status_code, 200, r.json())
        tree = self.client.get('/api/ems/').json()['tree']
        self.assertEqual([(c['category'], c['label'], c['pathOverride'], c['sort_order'])
                          for c in tree],
                         [('hvac', 'Cooling', '/electrical/cooling', 0),
                          ('ht', 'High Tension', '/electrical/ht-panels', 1)])

    def test_an_empty_category_flagged_hide_when_empty_is_left_out(self):
        body = {'categories': [{'code': 'ht'}, {'label': 'Spare Bay', 'hide_when_empty': True}]}
        self.push(body, commit=True)
        tree = self.client.get('/api/ems/').json()['tree']
        self.assertEqual([c['category'] for c in tree], ['ht'])

    def test_a_hidden_category_returns_with_its_code(self):
        self.push({'categories': [{'code': 'ht'}]}, commit=True)
        self.assertFalse(NavCategory.objects.get(code='utility').active)
        self.push({'categories': [{'code': 'utility'}]}, commit=True)
        self.assertTrue(NavCategory.objects.get(code='utility').active)
        self.assertEqual(NavCategory.objects.filter(code__startswith='utility').count(), 1)

    def test_moving_a_panel_gives_it_the_new_category_kind_unless_one_is_given(self):
        moved = {'categories': [{'code': 'ups', 'panels': [{'mfm_id': self.a.pk}]}]}
        self.push(moved, commit=True)
        self.assertEqual(PanelConfig.objects.get(group=self.a).panel_kind, 'ups_panel')
        explicit = {'categories': [{'code': 'production', 'panels': [
            {'mfm_id': self.a.pk, 'panel_kind': 'amf_panel'}]}]}
        self.push(explicit, commit=True)
        self.assertEqual(PanelConfig.objects.get(group=self.a).panel_kind, 'amf_panel')

    def test_two_categories_may_share_a_panel_kind(self):
        body = {'categories': [
            {'code': 'ht', 'panels': [{'name': 'HT-1'}]},
            {'label': 'HT Yard', 'panel_kind': 'ht_panel', 'panels': [{'name': 'HT-2'}]}]}
        self.push(body, commit=True)
        tree = self.client.get('/api/ems/').json()['tree']
        self.assertEqual([[p['label'] for p in c['children']] for c in tree],
                         [['HT-1'], ['HT-2']])

    def test_a_retired_panel_named_again_comes_back_with_its_id(self):
        self.push({'categories': [{'code': 'pcc'}]}, commit=True)
        self.assertFalse(Group.objects.get(pk=self.a.pk).active)
        self.push({'categories': [{'code': 'pcc', 'panels': [{'name': 'Panel A'}]}]}, commit=True)
        self.assertTrue(Group.objects.get(pk=self.a.pk).active)
        self.assertEqual(Group.objects.filter(name='Panel A').count(), 1)


class StandaloneAndPlainGroupTests(PushCase):
    def test_a_meter_out_of_a_retired_panel_shows_standalone(self):
        body = {'categories': [{'code': 'utility', 'meters': [{'mfm_id': eid(self.m[3])}]}]}
        r = self.push(body, commit=True)
        self.assertEqual(r.status_code, 200, r.json())
        tree = self.client.get('/api/ems/').json()['tree']
        self.assertEqual([(c['category'], [n['label'] for n in c['children']]) for c in tree],
                         [('utility', ['MFM-3'])])

    def test_a_plain_group_still_showing_an_unlisted_meter_is_retired(self):
        yard = Group.objects.create(name='Yard', slug='yard', nav_category='hvac')
        Membership.objects.create(group=yard, mfm=self.m[4])
        r = self.push({'categories': [{'code': 'hvac'}]}, commit=True)
        self.assertIn(('group', 'retire', 'Yard'),
                      {(c['entity'], c['action'], c['name']) for c in r.json()['changes']})
        self.assertEqual(self.client.get('/api/ems/').json()['leaf_count'], 0)


class ValidationTests(PushCase):
    def test_staff_credentials_are_required(self):
        self.assertEqual(self.push(self.body(), auth=None).status_code, 401)
        get_user_model().objects.create_user('viewer', password='pw')
        self.assertEqual(self.push(self.body(), auth='viewer:pw').status_code, 401)

    def test_every_error_is_reported_and_nothing_is_written(self):
        body = self.body()
        body['categories'][0]['panels'][0]['incomers'] = []                 # typo'd role
        body['categories'][0]['meters'] = [{'mfm_id': eid(self.m[1])}]     # also in a panel
        body['categories'][1]['panels'][0]['incoming'].append({'mfm_id': 1_009_999})
        body['categories'].append({'code': 'pcc'})                            # twice
        r = self.push(body, commit=True)
        self.assertEqual(r.status_code, 400)
        joined = '\n'.join(self.messages(r))
        for needle in ('unknown key(s) incomers', 'pick one', 'MFM 1009999 does not exist',
                       'code "pcc" is already used'):
            self.assertIn(needle, joined)
        self.assertTrue(Group.objects.get(pk=self.old.pk).active)
        self.assertNotIn('nav', r.json())

    def test_an_unknown_schema_and_table_are_refused(self):
        body = {'categories': [{'code': 'pcc', 'meters': [
            {'table_name': 'no_such_table'}, {'mfm_id': eid(self.m[1]), 'schema': 'sch_NOPE'}]}]}
        joined = '\n'.join(self.messages(self.push(body)))
        self.assertIn('"no_such_table" is not a migrated LoggerFast table', joined)
        self.assertIn('sch_NOPE v1 does not exist', joined)

    def test_a_stored_schema_version_cannot_be_redefined(self):
        self.push({'schemas': [SCHEMA], 'categories': [{'code': 'pcc'}]}, commit=True)
        changed = copy.deepcopy(SCHEMA)
        changed['fields'][0]['scale'] = 1.0
        r = self.push({'schemas': [changed], 'categories': [{'code': 'pcc'}]}, commit=True)
        self.assertEqual(r.status_code, 400)
        self.assertIn('immutable', '\n'.join(self.messages(r)))
        self.assertEqual(LoggerSchema.objects.get(code='sch_TEST').fields.get(
            name='voltage_avg').scale, 0.1)

    def test_an_empty_push_cannot_wipe_the_sidebar(self):
        r = self.push({'categories': []}, commit=True)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(NavCategory.objects.get(code='ht').active)

    def test_one_meter_twice_in_a_panel_is_refused(self):
        body = {'categories': [{'code': 'pcc', 'panels': [
            {'mfm_id': self.a.pk, 'incoming': [{'mfm_id': eid(self.m[1])}],
             'spare': [{'table_name': 'table_01'}]}]}]}
        self.assertIn('one role per panel', '\n'.join(self.messages(self.push(body))))
