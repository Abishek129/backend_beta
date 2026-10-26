import sqlite3
from unittest.mock import patch

from django.test import SimpleTestCase

from core.services import _energy_delta_sql, _metric_sql


class EnergyAliasTests(SimpleTestCase):
    def test_real_logger_mixed_case_counter_deltas(self):
        # Execute the generated aggregate against measured counter changes.
        # SQLite supports these aggregate expressions; PostgreSQL lookup is
        # checked separately through the exact generated identifier spelling.
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE readings ("active_energy_import_kWh" REAL, '
                         '"reactive_energy_import_kVArh" REAL)')
            conn.executemany('INSERT INTO readings VALUES (?, ?)',
                             [(25154128, 15778855), (25155536, 15781194)])
            have = {'active_energy_import_kWh', 'reactive_energy_import_kVArh'}
            for canonical, actual, expected in (
                ('active_energy_import_kwh', 'active_energy_import_kWh', 1408),
                ('reactive_energy_import_kvarh', 'reactive_energy_import_kVArh', 2339),
            ):
                with self.subTest(column=canonical), patch('core.services.is_real', return_value=True):
                    expr = _metric_sql('logger', 'delta', canonical, have)
                    self.assertIn(f'"{actual}"', expr)
                    self.assertEqual(conn.execute(f'SELECT {expr} FROM readings').fetchone()[0], expected)

    def test_canonical_column_wins_when_both_spellings_exist(self):
        expr = _energy_delta_sql('active_energy_import_kwh',
                                {'active_energy_import_kwh', 'active_energy_import_kWh'})
        self.assertIn('"active_energy_import_kwh"', expr)
        self.assertNotIn('"active_energy_import_kWh"', expr)

    def test_direction_and_scaling_with_aliased_import(self):
        with sqlite3.connect(':memory:') as conn:
            conn.execute('CREATE TABLE readings ("active_energy_import_kWh" REAL, '
                         '"active_energy_export_kwh" REAL)')
            conn.executemany('INSERT INTO readings VALUES (?, ?)', [(100, 20), (130, 25)])
            have = {'active_energy_import_kWh', 'active_energy_export_kwh'}
            for direction, expected in [('sum', 70), ('import', 60), ('export', 10), ('net', 50)]:
                with self.subTest(direction=direction):
                    expr = _energy_delta_sql('active_energy_import_kwh', have, (direction, 2, 1))
                    self.assertEqual(conn.execute(f'SELECT {expr} FROM readings').fetchone()[0], expected)

    def test_export_meter_reactive_energy_still_uses_import(self):
        expr = _energy_delta_sql('reactive_energy_import_kvarh',
                                {'reactive_energy_import_kVArh'}, ('export', 1, 1))
        self.assertIn('"reactive_energy_import_kVArh"', expr)

    def test_absent_registers_keep_existing_zero_behavior(self):
        self.assertEqual(_energy_delta_sql('active_energy_import_kwh', set()), '0')
