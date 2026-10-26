from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook

from core.logger_schemas import (
    install_schema_catalog,
    parse_schema_details,
    schema_assignment_rows,
)


class Command(BaseCommand):
    help = 'Install LoggerFast schema fields and assign MFMs from the audited workbook.'

    def add_arguments(self, parser):
        parser.add_argument('--details', default='mfm_schema_details.md')
        parser.add_argument('--workbook', default='ems_import_template.xlsx')
        parser.add_argument(
            '--categories', nargs='+',
            default=['HT', 'AMF', 'PCC', 'UPS', 'UTILITY', 'PRODUCTION'],
            help='Only these Meters-sheet categories are assigned.',
        )

    def handle(self, *args, **options):
        details_path = Path(options['details'])
        workbook_path = Path(options['workbook'])
        try:
            specs = parse_schema_details(details_path.read_text(encoding='utf-8'))
            workbook = load_workbook(workbook_path, read_only=True, data_only=True)
            assignments = schema_assignment_rows(workbook, options['categories'])
            result = install_schema_catalog(
                specs, assignments, source=details_path.name)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if 'workbook' in locals():
                workbook.close()

        self.stdout.write(self.style.SUCCESS(
            'Logger schemas installed: '
            f'{len(specs)} schemas / {sum(len(s.fields) for s in specs)} fields; '
            f'{result["mfms_assigned"]} MFMs assigned, '
            f'{result["mfms_unchanged"]} already current.'
        ))
