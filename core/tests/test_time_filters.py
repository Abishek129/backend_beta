import datetime as dt

from django.test import SimpleTestCase

from core.time_filters import (TimeFilterError, TimeFilterPolicy,
                               resolve_time_filter)


UTC = dt.timezone.utc
ANCHOR = dt.datetime(2026, 9, 21, 6, 30, tzinfo=UTC)  # noon IST


class TimeFilterTests(SimpleTestCase):
    def test_aliases_resolve_to_a_canonical_range_and_sampling(self):
        value = resolve_time_filter(
            {'range': 'last-7-days', 'sampling': 'day', 'shift': 'b'},
            TimeFilterPolicy(), ANCHOR)
        self.assertEqual((value.range, value.sampling, value.shift), ('last_7d', 'day', 'b'))
        self.assertLess(value.start, value.end)

    def test_frontend_sampling_alias_is_canonicalized(self):
        value = resolve_time_filter(
            {'range': 'custom_range', 'start_date': '2026-09-01',
             'end_date': '2026-09-21', 'sampling': 'daily'},
            TimeFilterPolicy(), ANCHOR, strict=True)
        self.assertEqual((value.range, value.sampling), ('custom', 'day'))

    def test_custom_dates_include_the_selected_end_day(self):
        value = resolve_time_filter(
            {'range': 'custom', 'start': '2026-09-01', 'end': '2026-09-03',
             'sampling': 'day'}, TimeFilterPolicy(), ANCHOR)
        self.assertEqual((value.end - value.start).total_seconds(), 3 * 86400)

    def test_strict_invalid_command_is_rejected(self):
        with self.assertRaises(TimeFilterError):
            resolve_time_filter({'range': 'today', 'sampling': 'month'},
                                TimeFilterPolicy(), ANCHOR, strict=True)
        with self.assertRaises(TimeFilterError):
            resolve_time_filter({'range': 'something-new'},
                                TimeFilterPolicy(), ANCHOR, strict=True)
        with self.assertRaises(TimeFilterError):
            resolve_time_filter({'range': 'custom', 'start': 'bad', 'end': 'worse'},
                                TimeFilterPolicy(), ANCHOR, strict=True)

    def test_range_only_policy_rejects_sampling_and_shift(self):
        policy = TimeFilterPolicy(supports_sampling=False, supports_shift=False)
        with self.assertRaises(TimeFilterError):
            resolve_time_filter({'sampling': 'hour'}, policy, ANCHOR, strict=True)
