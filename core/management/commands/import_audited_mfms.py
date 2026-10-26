from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from openpyxl import load_workbook

from core.audited_workbook import audited_meter_rows, install_audited_meters
from core.logger_devices import devices


class Command(BaseCommand):
    help = 'Import the audited MFM workbook with the confirmed plant hierarchy rules.'

    def add_arguments(self, parser):
        parser.add_argument('--workbook', default='ems_import_template.xlsx')
        parser.add_argument('--commit', action='store_true', help='Write changes (default: preview).')

    def handle(self, *args, **options):
        workbook = None
        try:
            workbook = load_workbook(Path(options['workbook']), read_only=True, data_only=True)
            rows = audited_meter_rows(workbook)
            report = install_audited_meters(
                rows, devices(strict=True), commit=options['commit'])
        except (OSError, KeyError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if workbook is not None:
                workbook.close()
        mode = 'COMMITTED' if report['committed'] else 'PREVIEW'
        self.stdout.write(self.style.SUCCESS(
            f'{mode}: {report["rows"]} rows; {report["created"]} create, '
            f'{report["updated"]} update, {report["memberships"]} panel memberships, '
            f'{report["production_panels_retired"]} production panels retired.'))
