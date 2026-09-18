"""The nav-category axis: the table's integrity and the classifier's caution.

No DB — ``core.nav_categories`` is deliberately Django-free so the importer can
classify in its pure ``build()`` stage.
"""
from django.test import SimpleTestCase

from core.nav_categories import (LOW_CONFIDENCE, NAV_CATEGORIES,
                                 NAV_CATEGORY_CHOICES, NAV_CATEGORY_CODES,
                                 NAV_CATEGORY_SLUGS, NAV_CATEGORY_TOKENS,
                                 PANEL_KIND_CATEGORY, PANEL_KIND_TOKENS,
                                 classify)


def category_of(slug, name=''):
    """The sidebar category a row lands in, whichever axis decides it."""
    kind, category, _token = classify(slug, name)
    return PANEL_KIND_CATEGORY.get(kind) or category


class TableIntegrityTests(SimpleTestCase):
    def test_the_eight_categories_are_fixed_and_ordered(self):
        self.assertEqual([cid for cid, _l, _s in NAV_CATEGORIES],
                         ['ht', 'amf', 'pcc', 'apfc', 'ups', 'utility',
                          'production', 'hvac'])

    def test_every_token_maps_to_a_declared_destination(self):
        self.assertLessEqual(set(PANEL_KIND_TOKENS.values()), set(PANEL_KIND_CATEGORY))
        self.assertEqual(set(NAV_CATEGORY_TOKENS.values()), NAV_CATEGORY_CODES)

    def test_the_two_axes_partition_the_eight_categories(self):
        both = set(PANEL_KIND_CATEGORY.values()) | NAV_CATEGORY_CODES
        self.assertEqual(both, {cid for cid, _l, _s in NAV_CATEGORIES})
        self.assertFalse(set(PANEL_KIND_CATEGORY.values()) & NAV_CATEGORY_CODES,
                         'a category must be placed by exactly one axis')

    def test_no_token_lives_in_both_axes(self):
        self.assertFalse(set(PANEL_KIND_TOKENS) & set(NAV_CATEGORY_TOKENS))

    def test_low_confidence_tokens_all_exist(self):
        self.assertLessEqual(LOW_CONFIDENCE,
                             set(PANEL_KIND_TOKENS) | set(NAV_CATEGORY_TOKENS))

    def test_values_fit_their_columns(self):
        # Group.nav_category / MFM.nav_category are CharField(max_length=16),
        # PanelConfig.panel_kind is CharField(max_length=24).
        self.assertLessEqual(max(len(c) for c, _l in NAV_CATEGORY_CHOICES), 16)
        self.assertLessEqual(max(len(k) for k in PANEL_KIND_CATEGORY), 24)

    def test_slugs_are_distinct(self):
        self.assertEqual(len(NAV_CATEGORY_SLUGS), len(NAV_CATEGORIES))


class ClassifierTests(SimpleTestCase):
    def test_one_known_slug_per_category(self):
        for slug, want in [('11kv-ht-panel-01', 'ht'),
                           ('amf-1-panel', 'amf'),
                           ('pcc-1a', 'pcc'),
                           ('apfcr-pcc1-hhf01', 'apfc'),
                           ('ups-output-p1', 'ups'),
                           ('mrpdb-panel', 'utility'),
                           ('bpdb-01-lamination-01-02', 'production'),
                           ('ahu-panel-03', 'hvac')]:
            with self.subTest(slug=slug):
                self.assertEqual(category_of(slug), want)

    def test_the_plant_outline_classifies_as_drawn(self):
        """Every row of the panel outline lands where the sheet puts it."""
        for slug, want in [('33-kv-ht-panel', 'ht'),
                           ('amf-2-panel', 'amf'),
                           ('pcc-3-panel', 'pcc'),
                           ('apfcr-2-panel', 'apfc'),
                           ('module-ups-outgoing-panel', 'ups'),
                           ('utility-panel', 'utility'),
                           ('pre-laminator-pdb-1', 'production'),
                           ('post-laminator-pdb-2', 'production'),
                           ('u-laminator-pdb-1', 'production'),
                           ('180-flipper-hiptop-pdb', 'production'),
                           ('bopt-pdb', 'production'),
                           ('air-washer-1', 'hvac'),
                           ('aw-exhaust-01', 'hvac'),
                           ('chiller-panel-02', 'hvac')]:
            with self.subTest(slug=slug):
                self.assertEqual(category_of(slug), want)

    def test_air_compressor_pdb_is_production_not_hvac(self):
        """The outline files "Air Compressor PDB-1/2" under Production.

        'compressor' is deliberately absent from the token table: mapping it to
        hvac (as backend2's BMS taxonomy would suggest) beats the 'pdb' token
        and files these two panels in the wrong category.
        """
        self.assertEqual(category_of('air-compressor-pdb-1'), 'production')
        self.assertEqual(category_of('air-compressor-pdb-2'), 'production')

    def test_no_match_returns_blank_rather_than_a_guess(self):
        for slug in ['block-a', 'main-incomer', 'feeder-7', 'panel', '', 'yard']:
            with self.subTest(slug=slug):
                self.assertEqual(classify(slug), ('', '', ''))

    def test_a_substring_never_matches(self):
        """The regression suite against backend2's canon() style.

        seed_plant.py:69 strips separators then substring-tests, which is how
        'ups' in 'groups' and 'ht' in 'light' become silent miscategorisations.
        """
        for slug in ['groups', 'ledge', 'light-switch', 'lawn-mower',
                     'lighthouse', 'drupsdb']:
            with self.subTest(slug=slug):
                self.assertEqual(category_of(slug), '')

    def test_dg_is_not_guessed(self):
        """backend2 files "11KV DG SYNC Panel" under HT Panels, not AMF —
        so a DG-named row must be assigned by hand, never guessed."""
        self.assertEqual(category_of('dg-panel-1'), '')
        # ...but the 11kv token still decides the real backend2 name correctly.
        self.assertEqual(category_of('11kv-dg-sync-panel'), 'ht')

    def test_a_stopword_never_shadows_the_informative_token(self):
        self.assertEqual(category_of('panel-ahu-3'), 'hvac')
        self.assertEqual(category_of('main-incomer-pcc-2a'), 'pcc')
        self.assertEqual(category_of('lt-panel-chiller-1'), 'hvac')

    def test_a_trailing_number_is_stemmed_off(self):
        self.assertEqual(category_of('pcc1'), 'pcc')
        self.assertEqual(category_of('ahu03'), 'hvac')

    def test_a_numeric_leading_token_is_kept_whole(self):
        self.assertEqual(category_of('11kv-bus'), 'ht')

    def test_the_matched_token_is_reported(self):
        self.assertEqual(classify('ahu-panel-03')[2], 'ahu')
        self.assertEqual(classify('pcc-1a')[2], 'pcc')

    def test_exactly_one_axis_is_ever_returned(self):
        for slug in ['pcc-1a', 'ahu-1', 'block-a', 'ups-1', 'bopt-pdb']:
            kind, category, _t = classify(slug)
            with self.subTest(slug=slug):
                self.assertFalse(kind and category,
                                 'a row must be placed by one axis, not both')

    def test_the_name_is_the_fallback_when_the_slug_is_uninformative(self):
        self.assertEqual(category_of('item', 'AHU Panel 3'), 'hvac')

    def test_classification_is_deterministic(self):
        self.assertEqual(classify('apfcr-pcc1-hhf01'), classify('apfcr-pcc1-hhf01'))
