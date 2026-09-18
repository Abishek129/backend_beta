"""Importer: the GROUPS_AND_MFMS.md section 8 worked example, end to end.

Fixtures in ``fixtures/`` are the four API payloads; FakeLoggerClient serves
them with the same five read methods as core.logger_client.LoggerClient.
"""
import copy
import io
import json
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core import logger_client
from core.importer import apply, build, diff, fetch, report
from core.logger_client import LoggerApiError, LoggerClient
from core.models import (DataSource, FeedEdge, Group, Membership, MFM,
                         MfmConfig, PanelConfig)
from core.nav_categories import NAV_CATEGORIES

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
DSN = 'postgresql://postgres@127.0.0.1:5433/target_version1?options=-csearch_path%3Dneuract'
DSN_BY_TARGET = {'*': DSN}

G_PLANT = 'grp_1788998400000'
G_YARD = 'grp_1788998400001'
G_BLOCK = 'grp_1788998400002'
G_MCC1 = 'grp_1788998400003'
G_MCC2 = 'grp_1788998400004'
G_LDB = 'grp_1788998400005'

D1 = 'dev_1788998400001'
D2 = 'dev_1788998400002'
D3 = 'dev_1788998400003'
D4 = 'dev_1788998400004'
D5 = 'dev_1788998400005'
D6 = 'dev_1788998400006'
D8 = 'dev_1788998400008'
D9 = 'dev_1788998400009'


def load(name):
    with (FIXTURES / name).open() as fh:
        return json.load(fh)


class FakeLoggerClient:
    """LoggerClient's five read methods, served from the fixtures."""

    def __init__(self, tree=None, devices=None, tables=None, edges=None, twins=None):
        self._tree = load('tree_small.json') if tree is None else tree
        self._devices = load('devices.json') if devices is None else devices
        self._tables = load('tables.json') if tables is None else tables
        self._edges = load('edges.json') if edges is None else edges
        self._twins = load('twins.json') if twins is None else twins
        self.calls = []

    def devices(self):
        self.calls.append('devices')
        return copy.deepcopy(self._devices)

    def tables(self, status='migrated'):
        self.calls.append(f'tables:{status}')
        rows = copy.deepcopy(self._tables)
        return [t for t in rows if not status or t.get('status') == status]

    def groups_tree(self):
        self.calls.append('groups_tree')
        return copy.deepcopy(self._tree)

    def group_twins(self, gid):
        self.calls.append(f'twins:{gid}')
        return copy.deepcopy(self._twins.get(gid, []))

    def device_edges(self):
        self.calls.append('device_edges')
        return copy.deepcopy(self._edges)


class ImporterCase(TestCase):
    """Shared driver: fetch -> diff -> apply against a FakeLoggerClient."""

    def run_import(self, client=None, **kwargs):
        client = client or FakeLoggerClient()
        desired = fetch(client, DSN_BY_TARGET, **kwargs)
        d = diff(desired)
        summary = apply(desired, d)
        return desired, d, summary

    def dry_run(self, client=None, **kwargs):
        client = client or FakeLoggerClient()
        desired = fetch(client, DSN_BY_TARGET, **kwargs)
        return desired, diff(desired)


# --------------------------------------------------------------------------
class TreeImportTests(ImporterCase):
    def setUp(self):
        self.run_import()

    def test_every_group_is_imported_with_its_parent(self):
        self.assertEqual(Group.objects.count(), 6)
        by_logger = {g.logger_id: g for g in Group.objects.all()}
        self.assertIsNone(by_logger[G_PLANT].parent)
        self.assertEqual(by_logger[G_YARD].parent, by_logger[G_PLANT])
        self.assertEqual(by_logger[G_BLOCK].parent, by_logger[G_PLANT])
        for gid in (G_MCC1, G_MCC2, G_LDB):
            self.assertEqual(by_logger[gid].parent, by_logger[G_BLOCK])

    def test_sort_order_and_panel_flags(self):
        by_logger = {g.logger_id: g for g in Group.objects.all()}
        self.assertEqual(by_logger[G_YARD].sort_order, 0)
        self.assertEqual(by_logger[G_BLOCK].sort_order, 1)
        self.assertEqual(by_logger[G_LDB].sort_order, 2)
        for gid in (G_PLANT, G_YARD, G_BLOCK):
            self.assertFalse(by_logger[gid].is_panel)
        for gid in (G_MCC1, G_MCC2, G_LDB):
            self.assertTrue(by_logger[gid].is_panel)

    def test_slugs_are_clean_and_tree_wide_unique(self):
        group_slugs = list(Group.objects.values_list('slug', flat=True))
        mfm_slugs = list(MFM.objects.values_list('slug', flat=True))
        self.assertEqual(sorted(group_slugs),
                         ['block-a', 'lighting-db', 'mcc-1', 'mcc-2', 'plant', 'yard'])
        self.assertEqual(MFM.objects.get(logger_id=D1).slug, 'mfm-01')
        self.assertEqual(len(set(group_slugs + mfm_slugs)), len(group_slugs) + len(mfm_slugs))

    def test_a_name_colliding_with_a_group_gets_the_parent_suffix(self):
        Group.objects.all().delete()
        MFM.objects.all().delete()
        devices = load('devices.json')
        devices[0]['name'] = 'MCC-1'          # a device named like a panel
        self.run_import(FakeLoggerClient(devices=devices))
        # the group keeps the clean slug, the meter is disambiguated
        self.assertEqual(Group.objects.get(logger_id=G_MCC1).slug, 'mcc-1')
        self.assertEqual(MFM.objects.get(logger_id=D1).slug, 'mcc-1-mcc-1')

    def test_mfms_carry_the_logger_device_fields(self):
        mfm = MFM.objects.get(logger_id=D3)
        self.assertEqual(mfm.name, 'MFM-03')
        self.assertEqual(mfm.protocol, 'modbus')
        self.assertEqual(mfm.gateway_id, 'gw_1')
        self.assertEqual(mfm.unit_id, 3)
        self.assertEqual(mfm.port, 502)
        self.assertEqual(mfm.logger_status, 'degraded')
        self.assertTrue(mfm.active)

    def test_ungrouped_device_is_imported_and_counted(self):
        desired, d = self.dry_run()
        self.assertEqual(desired.ungrouped_mfm_count, 1)
        self.assertEqual(d.ungrouped_mfm_count, 1)
        self.assertTrue(MFM.objects.filter(logger_id=D8, active=True).exists())
        self.assertFalse(Membership.objects.filter(mfm__logger_id=D8).exists())


# --------------------------------------------------------------------------
class MembershipTests(ImporterCase):
    def setUp(self):
        self.run_import()

    def test_one_mfm_in_two_panels_with_two_roles(self):
        rows = {m.group.logger_id: m for m in
                Membership.objects.select_related('group').filter(mfm__logger_id=D3)}
        self.assertEqual(set(rows), {G_MCC1, G_LDB})
        self.assertEqual(rows[G_MCC1].role, 'outgoing')
        self.assertEqual(rows[G_LDB].role, 'incoming')

    def test_plain_group_membership_has_no_role(self):
        row = Membership.objects.get(group__logger_id=G_YARD, mfm__logger_id=D9)
        self.assertIsNone(row.role)

    def test_roles_are_translated_and_positions_follow_the_devices_array(self):
        rows = list(Membership.objects.filter(group__logger_id=G_MCC1)
                    .select_related('mfm').order_by('position'))
        self.assertEqual([r.mfm.logger_id for r in rows], [D1, D2, D3])
        self.assertEqual([r.role for r in rows], ['incoming', 'outgoing', 'outgoing'])
        self.assertEqual([r.position for r in rows], [0, 1, 2])

    def test_membership_count(self):
        self.assertEqual(Membership.objects.count(), 8)


# --------------------------------------------------------------------------
class TableBindingTests(ImporterCase):
    def setUp(self):
        _, self.diff, _ = self.run_import()

    def test_one_migrated_table_binds_automatically(self):
        mfm = MFM.objects.get(logger_id=D1)
        self.assertEqual(mfm.table_binding, 'auto')
        self.assertEqual(mfm.table_name, 'mfm_01')
        self.assertEqual(mfm.logger_table_id, 'tbl_1788998400101')
        self.assertEqual(mfm.data_source.dsn, DSN)

    def test_duplicate_rows_with_the_same_name_still_bind(self):
        mfm = MFM.objects.get(logger_id=D2)
        self.assertEqual(mfm.table_binding, 'auto')
        self.assertEqual(mfm.table_name, 'mfm_02')

    def test_two_distinct_names_are_ambiguous_and_reported(self):
        mfm = MFM.objects.get(logger_id=D5)
        self.assertEqual(mfm.table_binding, 'ambiguous')
        self.assertEqual(mfm.table_name, '')
        self.assertIsNone(mfm.data_source)
        self.assertTrue(any('MFM-05' in p and 'ambiguous' in p for p in self.diff.problems))

    def test_no_migrated_table_leaves_the_binding_empty(self):
        mfm = MFM.objects.get(logger_id=D8)
        self.assertEqual(mfm.table_binding, 'none')
        self.assertEqual(mfm.table_name, '')
        self.assertIsNone(mfm.data_source)

    def test_one_data_source_per_db_target(self):
        self.assertEqual(DataSource.objects.count(), 1)
        row = DataSource.objects.get()
        self.assertEqual(row.logger_db_target_id, 'dbt_default')
        self.assertEqual(row.dsn, DSN)

    def test_a_target_without_a_dsn_is_reported_unbound(self):
        Group.objects.all().delete()
        MFM.objects.all().delete()
        DataSource.objects.all().delete()
        tables = load('tables.json')
        tables[0]['dbTargetId'] = 'dbt_other'
        tables[0]['dbTarget'] = {'id': 'dbt_other'}
        _, d, _ = self.run_import(FakeLoggerClient(tables=tables))
        self.assertTrue(any('dbt_' in p and 'no DSN' in p for p in d.problems))
        self.assertEqual(DataSource.objects.count(), 0)
        self.assertIsNone(MFM.objects.get(logger_id=D1).data_source)


# --------------------------------------------------------------------------
class EdgeAndTwinTests(ImporterCase):
    def setUp(self):
        self.run_import()

    def test_every_feed_edge_is_imported_with_its_type(self):
        edges = {(e.from_mfm.logger_id, e.to_mfm.logger_id): e.edge_type
                 for e in FeedEdge.objects.select_related('from_mfm', 'to_mfm')}
        self.assertEqual(edges, {
            (D1, D2): 'feeds', (D1, D3): 'feeds', (D3, D4): 'feeds',
            (D5, D6): 'feeds', (D5, D1): 'tie',
        })

    def test_twins_are_symmetric(self):
        mcc1 = Group.objects.get(logger_id=G_MCC1)
        mcc2 = Group.objects.get(logger_id=G_MCC2)
        self.assertEqual([g.logger_id for g in mcc1.twins.all()], [G_MCC2])
        self.assertEqual([g.logger_id for g in mcc2.twins.all()], [G_MCC1])
        self.assertEqual(Group.objects.get(logger_id=G_LDB).twins.count(), 0)

    def test_skip_flags_leave_twins_and_edges_untouched(self):
        FeedEdge.objects.all().delete()
        Group.objects.get(logger_id=G_MCC1).twins.clear()
        _, d, _ = self.run_import(include_twins=False, include_edges=False)
        self.assertTrue(d.twins.skipped)
        self.assertTrue(d.edges.skipped)
        self.assertEqual(FeedEdge.objects.count(), 0)
        self.assertEqual(Group.objects.get(logger_id=G_MCC1).twins.count(), 0)


# --------------------------------------------------------------------------
class IdempotencyTests(ImporterCase):
    def test_a_second_run_is_all_zero_and_keeps_every_id(self):
        self.run_import()
        before = {
            'groups': dict(Group.objects.values_list('logger_id', 'id')),
            'mfms': dict(MFM.objects.values_list('logger_id', 'id')),
        }
        _, second, summary = self.run_import()
        for name, entity in second.entities.items():
            self.assertEqual(len(entity.create), 0, f'{name} created on re-import')
            self.assertEqual(len(entity.update), 0, f'{name} updated on re-import')
            self.assertEqual(len(entity.retire), 0, f'{name} retired on re-import')
        self.assertEqual(dict(Group.objects.values_list('logger_id', 'id')), before['groups'])
        self.assertEqual(dict(MFM.objects.values_list('logger_id', 'id')), before['mfms'])
        for entity in ('groups', 'mfms', 'edges'):
            self.assertEqual(summary.get(entity, 'created'), 0)
            self.assertEqual(summary.get(entity, 'updated'), 0)
        self.assertEqual(summary.config_rows,
                         {'panel_configs_created': 0, 'mfm_configs_created': 0})

    def test_a_rename_is_an_update_not_a_new_row(self):
        self.run_import()
        pk = Group.objects.get(logger_id=G_MCC1).pk
        tree = load('tree_small.json')
        tree[0]['children'][1]['children'][0]['name'] = 'MCC-1A'
        _, d, _ = self.run_import(FakeLoggerClient(tree=tree))
        self.assertEqual(len(d.groups.create), 0)
        self.assertEqual(len(d.groups.update), 1)
        row = Group.objects.get(logger_id=G_MCC1)
        self.assertEqual(row.pk, pk)
        self.assertEqual(row.name, 'MCC-1A')
        self.assertEqual(row.slug, 'mcc-1a')

    def test_a_slug_swap_does_not_break_the_active_unique_index(self):
        self.run_import()
        tree = load('tree_small.json')
        panels = tree[0]['children'][1]['children']
        panels[0]['name'], panels[1]['name'] = panels[1]['name'], panels[0]['name']
        self.run_import(FakeLoggerClient(tree=tree))
        self.assertEqual(Group.objects.get(logger_id=G_MCC1).slug, 'mcc-2')
        self.assertEqual(Group.objects.get(logger_id=G_MCC2).slug, 'mcc-1')


# --------------------------------------------------------------------------
def _shrunk_fixtures():
    """The same plant minus Lighting DB and MFM-04 (and their table/edge)."""
    tree = load('tree_small.json')
    block = tree[0]['children'][1]
    block['children'] = [c for c in block['children'] if c['id'] != G_LDB]
    devices = [d for d in load('devices.json') if d['id'] != D4]
    tables = [t for t in load('tables.json') if t['deviceId'] != D4]
    edges = [e for e in load('edges.json') if e['toDeviceId'] != D4]
    twins = {k: v for k, v in load('twins.json').items() if k != G_LDB}
    return dict(tree=tree, devices=devices, tables=tables, edges=edges, twins=twins)


class VanishedRowTests(ImporterCase):
    def setUp(self):
        self.run_import()
        self.group_pk = Group.objects.get(logger_id=G_LDB).pk
        self.mfm_pk = MFM.objects.get(logger_id=D4).pk
        PanelConfig.objects.filter(group_id=self.group_pk).update(rated_kva=250.0)
        MfmConfig.objects.filter(mfm_id=self.mfm_pk).update(rated_kva=63.0)

    def test_vanished_rows_are_soft_retired_and_keep_their_config(self):
        _, d, _ = self.run_import(FakeLoggerClient(**_shrunk_fixtures()))
        self.assertEqual([e['key'] for e in d.groups.retire], [G_LDB])
        self.assertEqual([e['key'] for e in d.mfms.retire], [D4])

        group = Group.objects.get(pk=self.group_pk)
        mfm = MFM.objects.get(pk=self.mfm_pk)
        self.assertFalse(group.active)
        self.assertFalse(mfm.active)
        self.assertIsNotNone(group.retired_at)
        self.assertIsNotNone(mfm.retired_at)
        self.assertEqual(group.slug, 'lighting-db')          # slug handed back, id kept
        self.assertEqual(mfm.slug, 'mfm-04')

        self.assertFalse(Membership.objects.filter(group_id=self.group_pk).exists())
        self.assertFalse(Membership.objects.filter(mfm_id=self.mfm_pk).exists())
        self.assertFalse(FeedEdge.objects.filter(to_mfm_id=self.mfm_pk).exists())
        self.assertEqual(Membership.objects.count(), 6)
        self.assertEqual(FeedEdge.objects.count(), 4)

        self.assertEqual(PanelConfig.objects.get(group_id=self.group_pk).rated_kva, 250.0)
        self.assertEqual(MfmConfig.objects.get(mfm_id=self.mfm_pk).rated_kva, 63.0)

    def test_a_returning_row_keeps_its_id_and_goes_active_again(self):
        self.run_import(FakeLoggerClient(**_shrunk_fixtures()))
        _, d, summary = self.run_import()
        self.assertEqual(len(d.groups.create), 0)
        self.assertEqual(len(d.mfms.create), 0)
        self.assertEqual(summary.get('groups', 'reactivated'), 1)
        self.assertEqual(summary.get('mfms', 'reactivated'), 1)

        group = Group.objects.get(pk=self.group_pk)
        mfm = MFM.objects.get(pk=self.mfm_pk)
        self.assertTrue(group.active)
        self.assertTrue(mfm.active)
        self.assertIsNone(group.retired_at)
        self.assertIsNone(mfm.retired_at)
        self.assertEqual(Membership.objects.count(), 8)
        self.assertEqual(FeedEdge.objects.count(), 5)
        self.assertEqual(PanelConfig.objects.get(group_id=self.group_pk).rated_kva, 250.0)


# --------------------------------------------------------------------------
class ConfigRowTests(ImporterCase):
    def test_one_config_row_per_active_panel_and_meter(self):
        _, _, summary = self.run_import()
        self.assertEqual(PanelConfig.objects.count(), 3)      # MCC-1, MCC-2, Lighting DB
        self.assertEqual(MfmConfig.objects.count(), 8)
        self.assertEqual(summary.config_rows,
                         {'panel_configs_created': 3, 'mfm_configs_created': 8})
        self.assertFalse(PanelConfig.objects.filter(group__is_panel=False).exists())

    def test_config_rows_are_never_overwritten(self):
        self.run_import()
        PanelConfig.objects.filter(group__logger_id=G_MCC1).update(rated_kva=999.0)
        MfmConfig.objects.filter(mfm__logger_id=D1).update(device_type='transformer',
                                                           rated_kva=888.0)
        self.run_import()
        self.assertEqual(PanelConfig.objects.get(group__logger_id=G_MCC1).rated_kva, 999.0)
        cfg = MfmConfig.objects.get(mfm__logger_id=D1)
        self.assertEqual(cfg.rated_kva, 888.0)
        self.assertEqual(cfg.device_type, 'transformer')


# --------------------------------------------------------------------------
class ManualBindingTests(ImporterCase):
    def test_a_manual_binding_is_never_touched(self):
        self.run_import()
        MFM.objects.filter(logger_id=D1).update(
            table_binding='manual', table_name='hand_typed_table',
            logger_table_id='', data_source=None)
        _, d, summary = self.run_import()
        mfm = MFM.objects.get(logger_id=D1)
        self.assertEqual(mfm.table_binding, 'manual')
        self.assertEqual(mfm.table_name, 'hand_typed_table')
        self.assertIsNone(mfm.data_source)
        self.assertEqual(len(d.bindings.update), 0)
        self.assertEqual(summary.get('bindings', 'manual_kept'), 1)


# --------------------------------------------------------------------------
class RoleViolationTests(ImporterCase):
    def test_a_missing_role_in_a_panel_is_reported_and_not_written(self):
        tree = load('tree_small.json')
        tree[0]['children'][1]['children'][0]['devices'][1]['role'] = None
        _, d, _ = self.run_import(FakeLoggerClient(tree=tree))
        self.assertTrue(any('MCC-1' in p and 'no role' in p for p in d.problems))
        self.assertFalse(Membership.objects.filter(group__logger_id=G_MCC1,
                                                   mfm__logger_id=D2).exists())
        self.assertEqual(Membership.objects.count(), 7)

    def test_a_role_outside_a_panel_is_reported_and_not_written(self):
        tree = load('tree_small.json')
        tree[0]['children'][0]['devices'][0]['role'] = 'incomer'
        _, d, _ = self.run_import(FakeLoggerClient(tree=tree))
        self.assertTrue(any('Yard' in p and 'not a panel' in p for p in d.problems))
        self.assertFalse(Membership.objects.filter(group__logger_id=G_YARD).exists())

    def test_an_unknown_role_is_reported(self):
        tree = load('tree_small.json')
        tree[0]['children'][1]['children'][0]['devices'][2]['role'] = 'bus-coupler'
        _, d, _ = self.run_import(FakeLoggerClient(tree=tree))
        self.assertTrue(any('unknown role' in p for p in d.problems))


# --------------------------------------------------------------------------
class ReportTests(ImporterCase):
    def test_text_report_shows_the_counts_and_the_problems(self):
        _, d, summary = self.run_import()
        text = report(d, summary)
        self.assertIn('LoggerFast import — applied', text)
        self.assertIn('groups', text)
        self.assertIn('problems (', text)
        self.assertIn('ambiguous', text)
        self.assertIn('written:', text)

    def test_json_report_is_machine_readable(self):
        _, d, summary = self.run_import()
        payload = json.loads(report(d, summary, fmt='json'))
        self.assertFalse(payload['dry_run'])
        # the diff was taken before apply, so the first run lists all 6 as creates
        self.assertEqual(len(payload['entities']['groups']['create']), 6)
        self.assertEqual(payload['ungrouped_mfm_count'], 1)
        self.assertTrue(payload['problems'])

    def test_dry_run_report_says_nothing_was_written(self):
        _, d = self.dry_run()
        text = report(d, None)
        self.assertIn('DRY RUN', text)
        self.assertNotIn('written:', text)


# --------------------------------------------------------------------------
class ManagementCommandTests(ImporterCase):
    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch('core.management.commands.import_logger_hierarchy.LoggerClient',
                        return_value=FakeLoggerClient()):
            call_command('import_logger_hierarchy', *args, stdout=out, stderr=err)
        return out.getvalue()

    def test_dry_writes_nothing(self):
        text = self.call('--dry', '--dsn', DSN, '--allow-warnings')
        self.assertIn('DRY RUN', text)
        self.assertEqual(Group.objects.count(), 0)
        self.assertEqual(MFM.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(FeedEdge.objects.count(), 0)
        self.assertEqual(DataSource.objects.count(), 0)
        self.assertEqual(PanelConfig.objects.count(), 0)

    def test_a_real_run_writes_the_tree(self):
        self.call('--dsn', DSN, '--allow-warnings')
        self.assertEqual(Group.objects.count(), 6)
        self.assertEqual(MFM.objects.count(), 8)
        self.assertEqual(Membership.objects.count(), 8)
        self.assertEqual(FeedEdge.objects.count(), 5)

    def test_problems_exit_2_unless_allowed(self):
        with self.assertRaises(SystemExit) as ctx:
            self.call('--dry', '--dsn', DSN)
        self.assertEqual(ctx.exception.code, 2)

    def test_json_flag_prints_json(self):
        payload = json.loads(self.call('--dry', '--json', '--dsn', DSN, '--allow-warnings'))
        self.assertTrue(payload['dry_run'])
        self.assertEqual(len(payload['entities']['groups']['create']), 6)


# --------------------------------------------------------------------------
class BuildOnlyTests(TestCase):
    """build() is pure: same payloads in, same DesiredState out, no DB."""

    def test_build_is_deterministic(self):
        args = (load('tree_small.json'), load('devices.json'),
                [t for t in load('tables.json') if t['status'] == 'migrated'],
                DSN_BY_TARGET)
        first = build(*args, twins=load('twins.json'), edges=load('edges.json'))
        second = build(*args, twins=load('twins.json'), edges=load('edges.json'))
        self.assertEqual(first.groups, second.groups)
        self.assertEqual(first.mfms, second.mfms)
        self.assertEqual(first.memberships, second.memberships)
        self.assertEqual(first.bindings, second.bindings)
        self.assertEqual(first.edges, second.edges)
        self.assertEqual(first.twins, second.twins)

    def test_fetch_makes_exactly_the_documented_calls(self):
        client = FakeLoggerClient()
        fetch(client, DSN_BY_TARGET)
        self.assertEqual(client.calls[:3], ['devices', 'tables:migrated', 'groups_tree'])
        self.assertIn('device_edges', client.calls)
        self.assertEqual(sum(1 for c in client.calls if c.startswith('twins:')), 6)

    def test_skip_flags_skip_the_calls(self):
        client = FakeLoggerClient()
        desired = fetch(client, DSN_BY_TARGET, include_twins=False, include_edges=False)
        self.assertIsNone(desired.twins)
        self.assertIsNone(desired.edges)
        self.assertNotIn('device_edges', client.calls)
        self.assertFalse([c for c in client.calls if c.startswith('twins:')])


# --------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code=200, payload=None, text='body'):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class FakeSession:
    """Hands back queued responses and records what was asked for."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.gets = []
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return self.queue.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append((url, params, dict(headers or {})))
        return self.queue.pop(0)


TOKEN_OK = FakeResponse(payload={'access_token': 'tok-1', 'expires_in': 300})


class LoggerClientTests(TestCase):
    def test_public_calls_carry_no_token(self):
        session = FakeSession(FakeResponse(payload={'items': [{'id': 'dev_1'}]}))
        client = LoggerClient('http://logger:8003/', session=session)
        self.assertEqual(client.devices(), [{'id': 'dev_1'}])
        url, _, headers = session.gets[0]
        self.assertEqual(url, 'http://logger:8003/devices')
        self.assertNotIn('Authorization', headers)
        self.assertEqual(session.posts, [])

    def test_authed_calls_log_in_once_and_reuse_the_token(self):
        session = FakeSession(TOKEN_OK,
                              FakeResponse(payload={'items': []}),
                              FakeResponse(payload={'items': []}))
        client = LoggerClient('http://logger:8003', 'u', 'p', session=session)
        client.groups_tree()
        client.device_edges()
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.gets[0][2]['Authorization'], 'Bearer tok-1')
        self.assertEqual(session.gets[1][2]['Authorization'], 'Bearer tok-1')

    def test_a_401_triggers_one_re_login_and_one_retry(self):
        session = FakeSession(TOKEN_OK,
                              FakeResponse(status_code=401),
                              FakeResponse(payload={'access_token': 'tok-2', 'expires_in': 300}),
                              FakeResponse(payload={'items': [{'id': 'grp_1'}]}))
        client = LoggerClient('http://logger:8003', 'u', 'p', session=session)
        self.assertEqual(client.groups_tree(), [{'id': 'grp_1'}])
        self.assertEqual(len(session.posts), 2)
        self.assertEqual(session.gets[1][2]['Authorization'], 'Bearer tok-2')

    def test_an_expired_token_is_refreshed_before_the_call(self):
        session = FakeSession(TOKEN_OK,
                              FakeResponse(payload={'items': []}),
                              FakeResponse(payload={'access_token': 'tok-3', 'expires_in': 300}),
                              FakeResponse(payload={'items': []}))
        client = LoggerClient('http://logger:8003', 'u', 'p', session=session)
        client.groups_tree()
        client._expires_at = 0.0                     # 300 s later
        client.groups_tree()
        self.assertEqual(len(session.posts), 2)
        self.assertEqual(session.gets[1][2]['Authorization'], 'Bearer tok-3')

    def test_tables_pages_until_a_short_page(self):
        page = [{'id': f'tbl_{i}', 'name': f't{i}'} for i in range(2)]
        session = FakeSession(FakeResponse(payload={'items': page, 'total': 1}),
                              FakeResponse(payload={'items': page[:1], 'total': 1}))
        client = LoggerClient('http://logger:8003', session=session)
        with mock.patch.object(logger_client, '_PAGE_SIZE', 2):
            rows = client.tables()
        self.assertEqual(len(rows), 3)               # 'total' is ignored on purpose
        self.assertEqual([g[1]['page'] for g in session.gets], [1, 2])
        self.assertEqual(session.gets[0][1]['status'], 'migrated')

    def test_missing_credentials_and_bad_status_raise_logger_api_error(self):
        client = LoggerClient('http://logger:8003', session=FakeSession())
        with self.assertRaises(LoggerApiError):
            client.groups_tree()
        session = FakeSession(FakeResponse(status_code=503, text='upstream down'))
        client = LoggerClient('http://logger:8003', session=session)
        with self.assertRaises(LoggerApiError) as ctx:
            client.devices()
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn('upstream down', ctx.exception.body)

    def test_a_body_without_items_is_an_error(self):
        session = FakeSession(FakeResponse(payload={'success': True}))
        client = LoggerClient('http://logger:8003', session=session)
        with self.assertRaises(LoggerApiError):
            client.devices()


# --------------------------------------------------------------------------
# nav categories — the two placement axes, seeded but never clobbered
# --------------------------------------------------------------------------
class NavCategorySeedTests(ImporterCase):
    """The fixture exercises both axes: MCC-1/MCC-2 match 'mcc' -> pcc_panel
    (the panel_kind axis), Lighting DB matches 'lighting' -> utility (the
    nav_category axis), and Plant/Yard/Block A match nothing."""

    def setUp(self):
        self.run_import()

    def test_the_panel_kind_axis_is_seeded_on_the_config_row(self):
        self.assertEqual(PanelConfig.objects.get(group__logger_id=G_MCC1).panel_kind,
                         'pcc_panel')
        self.assertEqual(PanelConfig.objects.get(group__logger_id=G_MCC2).panel_kind,
                         'pcc_panel')

    def test_the_nav_category_axis_is_seeded_on_the_group(self):
        ldb = Group.objects.get(logger_id=G_LDB)
        self.assertEqual(ldb.nav_category, 'utility')
        self.assertEqual(ldb.nav_category_source, 'auto')

    def test_a_row_placed_by_panel_kind_keeps_a_blank_nav_category(self):
        # The axes are exclusive: a PCC panel is NOT also filed under a
        # nav_category, which is what "blank = placed by panel_kind" means.
        self.assertEqual(Group.objects.get(logger_id=G_MCC1).nav_category, '')

    def test_an_unrecognised_name_is_left_uncategorised_not_guessed(self):
        for logger_id in (G_PLANT, G_YARD, G_BLOCK):
            with self.subTest(logger_id=logger_id):
                self.assertEqual(Group.objects.get(logger_id=logger_id).nav_category, '')
        self.assertEqual(MFM.objects.get(logger_id=D1).nav_category, '')
        # ...and it stays on the default panel_kind too.
        self.assertEqual(PanelConfig.objects.get(group__logger_id=G_LDB).panel_kind,
                         'distribution_panel')


class NavCategoryManualTests(ImporterCase):
    def test_a_manual_nav_category_is_never_touched(self):
        self.run_import()
        Group.objects.filter(logger_id=G_LDB).update(
            nav_category='hvac', nav_category_source='manual')
        _, d, summary = self.run_import()
        row = Group.objects.get(logger_id=G_LDB)
        self.assertEqual(row.nav_category, 'hvac')          # NOT reseeded to utility
        self.assertEqual(row.nav_category_source, 'manual')
        self.assertEqual(len(d.entities['categories'].update), 0)
        self.assertGreaterEqual(summary.get('categories', 'manual_kept'), 1)

    def test_a_manual_nav_category_on_a_meter_is_never_touched(self):
        self.run_import()
        MFM.objects.filter(logger_id=D9).update(
            nav_category='production', nav_category_source='manual')
        self.run_import()
        self.assertEqual(MFM.objects.get(logger_id=D9).nav_category, 'production')

    def test_a_pin_set_after_the_diff_still_defeats_apply(self):
        """Defence in depth: the 'auto' guard is in the SQL WHERE, not only in
        Python, so a decision that goes stale between diff() and apply() is
        refused by the database rather than merely skipped by us."""
        self.run_import()
        Group.objects.filter(logger_id=G_LDB).update(nav_category='', nav_category_source='auto')
        desired = fetch(FakeLoggerClient(), DSN_BY_TARGET)
        d = diff(desired)                                   # decided while 'auto'
        Group.objects.filter(logger_id=G_LDB).update(
            nav_category='hvac', nav_category_source='manual')
        apply(desired, d)
        self.assertEqual(Group.objects.get(logger_id=G_LDB).nav_category, 'hvac')

    def test_a_hand_corrected_panel_kind_is_never_reseeded(self):
        self.run_import()
        PanelConfig.objects.filter(group__logger_id=G_MCC1).update(panel_kind='ht_panel')
        self.run_import()
        self.assertEqual(PanelConfig.objects.get(group__logger_id=G_MCC1).panel_kind,
                         'ht_panel')


class NavCategoryRegressionTests(ImporterCase):
    def test_a_lost_match_keeps_the_stored_value_and_is_reported(self):
        """If the token table is edited (or the logger renames a row) so the
        heuristic no longer matches, an auto row must NOT be blanked."""
        self.run_import()
        self.assertEqual(Group.objects.get(logger_id=G_LDB).nav_category, 'utility')
        with mock.patch('core.importer.classify', return_value=('', '', '')):
            _, d, summary = self.run_import()
        self.assertEqual(Group.objects.get(logger_id=G_LDB).nav_category, 'utility')
        self.assertEqual(summary.get('categories', 'kept_unmatched'), 1)
        self.assertTrue(any('no longer matches' in p for p in d.problems))


class NavCategoryIdempotencyTests(ImporterCase):
    def test_a_second_run_reports_no_category_changes(self):
        self.run_import()
        _, d, _ = self.run_import()
        e = d.entities['categories']
        self.assertEqual((len(e.create), len(e.update), len(e.retire)), (0, 0, 0))

    def test_a_manual_category_survives_retire_and_revival(self):
        self.run_import()
        Group.objects.filter(logger_id=G_LDB).update(
            nav_category='hvac', nav_category_source='manual')
        empty = FakeLoggerClient(tree=[])
        self.run_import(client=empty)                       # soft-retire everything
        row = Group.objects.get(logger_id=G_LDB)
        self.assertFalse(row.active)
        self.assertEqual(row.nav_category, 'hvac')          # retire must not clear it
        self.run_import()                                   # revive
        row.refresh_from_db()
        self.assertTrue(row.active)
        self.assertEqual(row.nav_category, 'hvac')


class NavCategoryCensusTests(ImporterCase):
    def test_the_census_names_every_category_even_at_zero(self):
        _, d = self.dry_run()
        counts = d.category_census['counts']
        for cid, _label, _slug in NAV_CATEGORIES:
            self.assertIn(cid, counts)
        self.assertEqual(counts['pcc']['panels'], 2)        # MCC-1, MCC-2
        self.assertEqual(counts['utility']['panels'], 1)    # Lighting DB

    def test_uncategorised_rows_are_listed_for_the_operator(self):
        _, d = self.dry_run()
        slugs = {r['slug'] for r in d.category_census['uncategorised']}
        self.assertIn('mfm-09', slugs)                      # a loose, unnamed meter
        self.assertNotIn('lighting-db', slugs)              # placed by nav_category
        self.assertNotIn('mcc-1', slugs)                    # placed by panel_kind

    def test_low_confidence_rows_name_the_token_that_decided_them(self):
        _, d = self.dry_run()
        rows = {r['slug']: r for r in d.category_census['low_confidence']}
        self.assertEqual(rows['mcc-1']['token'], 'mcc')     # 'mcc' -> pcc is a guess
        self.assertEqual(rows['mcc-1']['category'], 'pcc')

    def test_the_text_report_prints_the_census(self):
        _, d = self.dry_run()
        text = report(d)
        self.assertIn('nav categories (from the heuristic)', text)
        self.assertIn('HVAC Panels', text)                  # printed even at zero
        self.assertIn('— uncategorised —', text)

    def test_the_json_report_carries_the_census(self):
        _, d = self.dry_run()
        payload = json.loads(report(d, fmt='json'))
        self.assertIn('categories', payload)
        self.assertIn('counts', payload['categories'])


class NavCategorySlugTests(ImporterCase):
    def test_a_group_cannot_steal_a_category_slug(self):
        """A LoggerFast group literally named "HVAC Panels" must not take
        'hvac-panels' from the category and send the FE's slug->node fallback
        to the wrong node."""
        tree = load('tree_small.json')
        tree[0]['name'] = 'HVAC Panels'
        desired = fetch(FakeLoggerClient(tree=tree), DSN_BY_TARGET)
        slug = next(g['slug'] for g in desired.groups if g['name'] == 'HVAC Panels')
        self.assertNotEqual(slug, 'hvac-panels')
        self.assertTrue(slug.startswith('hvac-panels-'))
