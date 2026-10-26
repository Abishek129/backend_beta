from pathlib import Path

from django.conf import settings
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from openpyxl import load_workbook

from core.logger_schemas import (
    install_schema_catalog,
    parse_schema_details,
    schema_assignment_rows,
)
from core.models import LoggerSchema, LoggerSchemaField, MFM


ROOT = Path(settings.BASE_DIR)


class LoggerSchemaCatalogTests(TestCase):
    @classmethod
    def specs(cls):
        return parse_schema_details(
            (ROOT / 'mfm_schema_details.md').read_text(encoding='utf-8'))

    def test_audited_details_parse_all_schemas_and_fields(self):
        specs = self.specs()
        self.assertEqual(
            [(schema.code, schema.logger_id, len(schema.fields)) for schema in specs],
            [
                ('sch_DG', 'sch_1789555418173', 45),
                ('sch_EM6400_NG+', 'sch_1788521250508', 73),
                ('sch_EMMS7_96', 'sch_1788773139707', 66),
                ('sch_LG2599', 'sch_1789627016559', 55),
            ],
        )
        self.assertEqual(sum(len(schema.fields) for schema in specs), 239)

    def test_workbook_assignments_cover_all_confirmed_mfm_categories(self):
        workbook = load_workbook(
            ROOT / 'ems_import_template.xlsx', read_only=True, data_only=True)
        self.addCleanup(workbook.close)
        rows = schema_assignment_rows(workbook)
        self.assertEqual(len(rows), 102)  # Two distinct AIR_COMPRESSOR_PDB meters are retained.
        counts = {code: sum(row[2] == code for row in rows)
                  for code in {row[2] for row in rows}}
        self.assertEqual(counts, {
            'sch_EM6400_NG+': 5, 'sch_EMMS7_96': 59, 'sch_LG2599': 38,
        })

    def test_install_is_idempotent_assigns_by_table_and_protects_schema(self):
        specs = self.specs()
        meter = MFM.objects.create(
            name='Main', slug='main', table_name='gic_01_n1_MAIN', sheet_row=2)
        assignments = ((2, 'Main', 'sch_EM6400_NG+'),)

        first = install_schema_catalog(specs, assignments)
        meter.refresh_from_db()
        self.assertEqual(first, {
            'schemas_created': 4, 'fields_created': 239,
            'mfms_assigned': 1, 'mfms_unchanged': 0,
        })
        self.assertEqual(meter.logger_schema.code, 'sch_EM6400_NG+')
        self.assertEqual(meter.logger_schema.timestamp_column, 'timestamp_utc')
        self.assertEqual(LoggerSchema.objects.count(), 4)
        self.assertEqual(LoggerSchemaField.objects.count(), 239)

        second = install_schema_catalog(specs, assignments)
        self.assertEqual(second, {
            'schemas_created': 0, 'fields_created': 0,
            'mfms_assigned': 0, 'mfms_unchanged': 1,
        })
        with self.assertRaises(ProtectedError):
            meter.logger_schema.delete()

    def test_an_existing_version_cannot_be_silently_redefined(self):
        specs = self.specs()
        install_schema_catalog(specs, ())
        field = LoggerSchemaField.objects.get(
            logger_schema__code='sch_EMMS7_96', name='frequency_hz')
        field.scale = 99
        field.save(update_fields=['scale'])
        with self.assertRaisesRegex(ValueError, 'immutable'):
            install_schema_catalog(specs, ())
