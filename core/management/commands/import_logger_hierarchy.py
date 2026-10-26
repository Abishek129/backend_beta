"""Import the LoggerFast group hierarchy into beta.

    manage.py import_logger_hierarchy --dry
    manage.py import_logger_hierarchy

Exits 2 when the run reported problems (ambiguous table bindings, role
violations, unbound db targets) unless --allow-warnings.
"""
import os
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.importer import DEFAULT_DSN_KEY, apply, diff, fetch, manual_slugs, report
from core.logger_client import LoggerApiError, LoggerClient

DEFAULT_URL = 'http://127.0.0.1:8003'
DEFAULT_DSN = ('postgresql://postgres@127.0.0.1:5433/target_version1'
               '?options=-csearch_path%3Dneuract')
DSN_ENV_PREFIX = 'LOGGER_TIMESERIES_DSN_'
EXIT_PROBLEMS = 2


class Command(BaseCommand):
    help = 'Import groups, MFMs, memberships, twins and feed edges from LoggerFast.'

    def add_arguments(self, parser):
        parser.add_argument('--url', default=None,
                            help=f'LoggerFast base URL (env LOGGER_API_URL, default {DEFAULT_URL}).')
        parser.add_argument('--user', default=None, help='Login for logger-read (env LOGGER_API_USER).')
        parser.add_argument('--password', default=None, help='Password (env LOGGER_API_PASSWORD).')
        parser.add_argument('--dsn', default=None,
                            help='Timeseries DSN for the default dbTarget (env LOGGER_TIMESERIES_DSN). '
                                 f'Extra targets: {DSN_ENV_PREFIX}<targetId>.')
        parser.add_argument('--dry', action='store_true', help='Report only; write nothing.')
        parser.add_argument('--json', action='store_true', help='Machine-readable report.')
        parser.add_argument('--skip-twins', action='store_true', help='Leave twins untouched.')
        parser.add_argument('--skip-edges', action='store_true', help='Leave feed edges untouched.')
        parser.add_argument('--allow-warnings', action='store_true',
                            help='Exit 0 even when problems were reported.')
        parser.add_argument('--fail-on-uncategorised', action='store_true',
                            help='Exit 2 when any active PANEL ends with no nav '
                                 'category. Off by default: an uncategorised row '
                                 'is a normal day-one state, and failing on it '
                                 'would train --allow-warnings into muscle memory '
                                 'and mask the real problems.')

    def handle(self, *args, **opts):
        url = (opts.get('url') or os.environ.get('LOGGER_API_URL')
               or getattr(settings, 'LOGGER_API_URL', '') or DEFAULT_URL)
        user = (opts.get('user') or os.environ.get('LOGGER_API_USER')
                or getattr(settings, 'LOGGER_API_USER', ''))
        password = (opts.get('password') or os.environ.get('LOGGER_API_PASSWORD')
                    or getattr(settings, 'LOGGER_API_PASSWORD', ''))
        client = LoggerClient(url, user, password,
                              timeout=float(getattr(settings, 'LOGGER_API_TIMEOUT', 15.0)))
        try:
            desired = fetch(client, self._dsn_by_target(opts),
                            include_twins=not opts['skip_twins'],
                            include_edges=not opts['skip_edges'],
                            reserved_slugs=manual_slugs())
        except LoggerApiError as exc:
            raise CommandError(f'LoggerFast at {url}: {exc}')

        result = diff(desired)
        summary = None if opts['dry'] else apply(desired, result)
        self.stdout.write(report(result, summary, fmt='json' if opts['json'] else 'text'))
        if result.problems and not opts['allow_warnings']:
            self.stderr.write(f'{len(result.problems)} problem(s) reported '
                              f'— rerun with --allow-warnings to ignore.')
            sys.exit(EXIT_PROBLEMS)
        if opts['fail_on_uncategorised']:
            stray = [r for r in (result.category_census.get('uncategorised') or [])
                     if r['kind'] == 'panel']
            if stray:
                self.stderr.write(f'{len(stray)} panel(s) have no nav category '
                                  f'— assign them in the admin.')
                sys.exit(EXIT_PROBLEMS)

    @staticmethod
    def _dsn_by_target(opts):
        """{'*': default dsn} plus one entry per LOGGER_TIMESERIES_DSN_<targetId>."""
        default = (opts.get('dsn') or os.environ.get('LOGGER_TIMESERIES_DSN')
                   or getattr(settings, 'LOGGER_TIMESERIES_DSN', '') or DEFAULT_DSN)
        out = {DEFAULT_DSN_KEY: default}
        for key, value in os.environ.items():
            if key.startswith(DSN_ENV_PREFIX) and value.strip():
                out[key[len(DSN_ENV_PREFIX):]] = value.strip()
        return out
