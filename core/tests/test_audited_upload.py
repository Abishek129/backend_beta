import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from openpyxl import Workbook

from core.models import Group, LoggerSchema, Membership, MFM


def workbook(rows):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Meters'
    sheet.append(['MFM name', 'Nav category', 'Distribution panel', 'Role',
                  'Table name', 'Nominal voltage (V)', 'Rated kVA', 'Rated kW',
                  'Rated current (A)', 'Frequency (Hz)', 'Contracted kW', 'schema',
                  'Logger device ID', 'Physical table'])
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


DEVICES = {
    'dev_1': {'id': 'dev_1', 'name': 'I/C_MAIN', 'table_name': 'gic_main',
              'table_id': 'table_1', 'db_target_id': 'pg_test', 'protocol': 'modbus',
              'gateway_id': 'gw', 'unit_id': 1, 'port': 502, 'status': 'connected'},
    'dev_2': {'id': 'dev_2', 'name': 'O/G_LINE', 'table_name': 'gic_line',
              'table_id': 'table_2', 'db_target_id': 'pg_test', 'protocol': 'modbus',
              'gateway_id': 'gw', 'unit_id': 2, 'port': 502, 'status': 'connected'},
}


class AdminAuditedUploadTests(TestCase):
    url = '/admin/core/mfm/import-workbook/'

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            'operator', 'operator@example.com', 'password')
        self.client.force_login(self.admin)
        LoggerSchema.objects.create(code='sch_test', version=1, name='Test')
        patcher = mock.patch('core.logger_devices.devices', return_value=DEVICES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, rows, action='preview'):
        file = SimpleUploadedFile('plant.xlsx', workbook(rows))
        return self.client.post(self.url, {'file': file, 'action': action})

    def test_preview_and_commit_create_panel_members_and_table_bindings(self):
        rows = [
            ['MFM-1', 'UPS', None, 'Incomer', 'I/C_MAIN', '415V', '1MVA',
             '900KW', '1200A', '50HZ', None, 'sch_test'],
            ['MFM-2', 'UPS', None, 'Outgoer', 'O/G_LINE', '415V', '500KVA',
             '450KW', '700A', '50HZ', None, 'sch_test'],
        ]
        response = self.upload(rows)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Preview only — no changes saved')
        self.assertEqual((Group.objects.count(), MFM.objects.count()), (0, 0))

        response = self.upload(rows, action='commit')
        self.assertContains(response, 'Import committed')
        panel = Group.objects.get(name='UPS')
        self.assertEqual(panel.config.panel_kind, 'ups_panel')
        self.assertEqual(dict(Membership.objects.values_list('mfm__name', 'role')),
                         {'I/C_MAIN': 'incoming', 'O/G_LINE': 'outgoing'})
        meter = MFM.objects.get(name='I/C_MAIN')
        self.assertEqual((meter.table_name, meter.logger_id, meter.logger_schema.code),
                         ('gic_main', 'dev_1', 'sch_test'))
        self.assertEqual(meter.config.rated_kva, 1000)
        self.assertEqual(meter.config.nominal_voltage_v, 415)

        self.upload(rows, action='commit')
        self.assertEqual((Group.objects.count(), MFM.objects.count(), Membership.objects.count()),
                         (1, 2, 2))

    def test_ambiguous_or_missing_table_is_rejected_atomically(self):
        rows = [
            ['MFM-1', 'PCC', 'Panel A', 'Incomer', 'I/C_MAIN', None, None,
             None, None, None, None, 'sch_test'],
            ['MFM-2', 'PCC', 'Panel A', 'Outgoer', 'UNKNOWN', None, None,
             None, None, None, None, 'sch_test'],
        ]
        response = self.upload(rows, action='commit')
        self.assertContains(response, 'Import rejected — no changes saved')
        self.assertEqual((Group.objects.count(), MFM.objects.count()), (0, 0))

    def test_explicit_physical_table_disambiguates_label(self):
        duplicate = {**DEVICES, 'dev_3': {**DEVICES['dev_2'], 'id': 'dev_3',
                                           'table_name': 'gic_line_2'}}
        rows = [['MFM-2', 'PCC', 'Panel A', 'Incomer', 'O/G_LINE', None, None,
                 None, None, None, None, 'sch_test', None, 'gic_line_2']]
        with mock.patch('core.logger_devices.devices', return_value=duplicate):
            response = self.upload(rows, action='commit')
        self.assertContains(response, 'Import committed')
        self.assertEqual(MFM.objects.get().table_name, 'gic_line_2')

    def test_requires_admin_session_and_csrf(self):
        anonymous = Client()
        response = anonymous.get(self.url)
        self.assertEqual(response.status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        response = csrf_client.post(self.url, {
            'file': SimpleUploadedFile('plant.xlsx', workbook([])),
            'action': 'commit',
        })
        self.assertEqual(response.status_code, 403)

    def test_rejects_production_panel(self):
        rows = [['MFM-1', 'Production', 'Wrong Panel', 'Incomer', 'I/C_MAIN',
                 None, None, None, None, None, None, 'sch_test']]
        response = self.upload(rows, action='commit')
        self.assertContains(response, 'Production MFMs must be standalone')
        self.assertEqual(MFM.objects.count(), 0)

    def test_new_named_panel_and_standalone_production(self):
        rows = [
            ['MFM-1', 'PCC', 'New Board', 'Incomer', 'I/C_MAIN', None, None,
             None, None, None, None, 'sch_test'],
            ['MFM-2', 'Production', None, None, 'O/G_LINE', None, None,
             None, None, None, None, 'sch_test'],
        ]
        response = self.upload(rows, action='commit')
        self.assertContains(response, 'Import committed')
        self.assertEqual(Group.objects.get(name='New Board').config.panel_kind, 'pcc_panel')
        production = MFM.objects.get(name='O/G_LINE')
        self.assertEqual(production.nav_category, 'production')
        self.assertFalse(production.memberships.exists())

    def test_staff_user_without_superuser_cannot_import(self):
        staff = get_user_model().objects.create_user(
            'staff', 'staff@example.com', 'password', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_mfm_changelist_links_to_upload(self):
        response = self.client.get('/admin/core/mfm/')
        self.assertContains(response, self.url)
