from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from openpyxl import load_workbook

from core.audited_workbook import audited_meter_rows


class AuditedWorkbookRulesTests(SimpleTestCase):
    def test_confirmed_panel_rules_and_engineering_units(self):
        workbook = load_workbook(
            Path(settings.BASE_DIR) / 'ems_import_template.xlsx',
            read_only=True, data_only=True)
        self.addCleanup(workbook.close)
        rows = audited_meter_rows(workbook)
        self.assertEqual(len(rows), 102)

        ht = [row for row in rows if row.category == 'ht']
        self.assertEqual({row.panel for row in ht}, {'33KV HT Panel'})
        self.assertEqual(ht[0].nominal_voltage_v, 33000)
        self.assertEqual(ht[0].rated_kva, 71450)

        ups = [row for row in rows if row.category == 'ups']
        self.assertEqual({row.panel for row in ups}, {'UPS'})
        self.assertEqual([row.role for row in ups].count('incoming'), 4)
        self.assertEqual([row.role for row in ups].count('outgoing'), 9)

        utility = [row for row in rows if row.category == 'utility']
        self.assertEqual({row.panel for row in utility}, {'Utility Panel'})
        self.assertEqual([row.role for row in utility].count('incoming'), 2)
        self.assertEqual([row.role for row in utility].count('outgoing'), 23)

        production = [row for row in rows if row.category == 'production']
        self.assertEqual(len(production), 17)
        self.assertTrue(all(not row.panel and not row.role for row in production))
        self.assertEqual(production[0].name, 'I/C_PCC_2_LAMINATOR_PDB')
