"""panels.dispatch — id/type routing and the 4404 reject path."""
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase

from config.asgi import application
from core.constants import FEEDER_ID_OFFSET
from core.models import MFM, Group, MfmConfig
from panels.dispatch import _resolve_type
from panels.registry import SCREENS


def resolve(eid):
    return async_to_sync(_resolve_type)(eid)


# PIN 2026-09-09: TransactionTestCase, not TestCase — database_sync_to_async runs
# close_old_connections(), which drops the connection inside TestCase's atomic wrapper.
class ResolveTypeTests(TransactionTestCase):
    def setUp(self):
        self.panel = Group.objects.create(logger_id='grp_p1', name='MCC-1', slug='mcc-1',
                                          is_panel=True)
        self.section = Group.objects.create(logger_id='grp_s1', name='Block A', slug='block-a')
        self.retired = Group.objects.create(logger_id='grp_r1', name='Old MCC', slug='old-mcc',
                                            is_panel=True, active=False)
        self.lt = MFM.objects.create(logger_id='dev_1', name='MFM-01', slug='mfm-01')
        MfmConfig.objects.create(mfm=self.lt, device_type='lt_panel')
        self.tx = MFM.objects.create(logger_id='dev_2', name='MFM-02', slug='mfm-02')
        MfmConfig.objects.create(mfm=self.tx, device_type='transformer')
        self.bare = MFM.objects.create(logger_id='dev_3', name='MFM-03', slug='mfm-03')
        self.gone = MFM.objects.create(logger_id='dev_4', name='MFM-04', slug='mfm-04',
                                       active=False)

    def test_panel_id_is_distribution_panel(self):
        self.assertEqual(resolve(self.panel.id), 'distribution_panel')

    def test_meter_id_is_lt_panel(self):
        self.assertEqual(resolve(self.lt.eid), 'lt_panel')

    def test_meter_id_is_transformer_from_config(self):
        self.assertEqual(resolve(self.tx.eid), 'transformer')

    def test_meter_without_config_defaults_to_lt_panel(self):
        self.assertEqual(resolve(self.bare.eid), 'lt_panel')

    def test_retired_panel_is_none(self):
        self.assertIsNone(resolve(self.retired.id))

    def test_retired_meter_is_none(self):
        self.assertIsNone(resolve(self.gone.eid))

    def test_plain_non_panel_group_is_none(self):
        self.assertIsNone(resolve(self.section.id))

    def test_unknown_ids_are_none(self):
        self.assertIsNone(resolve(999_999))                    # no such group
        self.assertIsNone(resolve(FEEDER_ID_OFFSET + 999_999))  # no such meter


class ScreensRegisteredTests(TestCase):
    def test_single_and_distribution_panel_screen_matrix(self):
        expected = {
            'overview', 'real-time-monitoring', 'energy-power', 'energy-summary',
            'power-energy-analysis', 'load-anomalies', 'voltage-current',
            'voltage-history', 'current-history', 'power-quality-summary',
            'power-quality-history', 'energy-distribution',
        }
        self.assertEqual(set(SCREENS), expected)
        shared = {'real-time-monitoring', 'energy-power', 'voltage-current',
                  'power-quality-summary'}
        for name in shared:
            self.assertEqual(set(SCREENS[name]),
                             {'distribution_panel', 'transformer', 'lt_panel'})
        self.assertEqual(set(SCREENS['energy-distribution']), {'distribution_panel'})
        for name in expected - shared - {'energy-distribution'}:
            self.assertEqual(set(SCREENS[name]), {'transformer', 'lt_panel'})


class Reject4404Tests(TransactionTestCase):
    """ASGI-level: the catch-all accepts, sends the error frame, closes 4404."""

    async def _connect_and_close(self, path):
        comm = WebsocketCommunicator(application, path)
        connected, _ = await comm.connect()
        self.assertTrue(connected, f'{path} was not accepted')
        frame = await comm.receive_json_from()
        code = await comm.receive_output()
        await comm.disconnect()
        return frame, code

    def test_non_numeric_id_rejected(self):
        frame, code = async_to_sync(self._connect_and_close)('/ws/mfm/abc/overview/')
        self.assertEqual(frame['type'], 'error')
        self.assertEqual(frame['code'], 4404)
        self.assertEqual(frame['mfm_id'], 'abc')
        self.assertEqual(code, {'type': 'websocket.close', 'code': 4404})

    def test_distribution_panel_is_rejected_by_single_mfm_screen(self):
        g = Group.objects.create(logger_id='grp_p9', name='MCC-9', slug='mcc-9', is_panel=True)
        frame, code = async_to_sync(self._connect_and_close)(f'/ws/mfm/{g.id}/overview/')
        self.assertEqual(frame['type'], 'error')
        self.assertEqual(frame['code'], 4404)
        self.assertEqual(code, {'type': 'websocket.close', 'code': 4404})

    async def _panel_snapshot(self, panel_id):
        comm = WebsocketCommunicator(
            application, f'/ws/mfm/{panel_id}/energy-distribution/')
        connected, _ = await comm.connect()
        frame = await comm.receive_json_from()
        await comm.disconnect()
        return connected, frame

    def test_distribution_panel_serves_aggregate_screen(self):
        panel = Group.objects.create(
            logger_id='grp_p10', name='PCC-10', slug='pcc-10', is_panel=True)
        connected, frame = async_to_sync(self._panel_snapshot)(panel.id)
        self.assertTrue(connected)
        self.assertEqual(frame['type'], 'snapshot')
        self.assertEqual(frame['mfm_id'], panel.id)
        self.assertEqual(frame['widgets']['sources'], [])
        self.assertEqual(frame['widgets']['consumers'], [])
