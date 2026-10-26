"""The nav sidebar axis — the eight fixed categories, and the name heuristic
that seeds them on import.

TWO sources place a panel, and they are ORTHOGONAL:

    PanelConfig.panel_kind  -> HT / AMF / PCC / UPS   (structural switchgear)
    Group.nav_category      -> HVAC / APFC / Production / Utility  (blank-able)

A meter has no panel_kind, so ``MFM.nav_category`` may name any of the eight.

A blank ``nav_category`` means "this row is placed by ``panel_kind``". Never
derive one axis from the other, and never widen one to cover the other:
``panel_kind`` says what a row IS (it drives the frame ``mfm_type`` and the
nameplate defaults), ``nav_category`` says WHERE IT IS FILED in the sidebar.

Replaces backend2's hardcoded ``core/ems_tree.py`` SECTIONS: the category LIST
is fixed here, but which rows land in it is DB state — seeded by the importer,
corrected in the admin — so a re-import never re-hardcodes the plant.

One definition, four consumers: ``core.models`` (choices), ``core.importer``
(seeding), ``core.admin`` (filter + bulk correction), ``core.nav`` (order).
"""
import re

# (category id, label, url slug) — THE ORDER IS THE SIDEBAR ORDER. Do not sort.
NAV_CATEGORIES = [
    ('ht',         'HT Panels',         'ht-panels'),
    ('amf',        'AMF Panels',        'amf-panels'),
    ('pcc',        'PCC Panels',        'pcc-panels'),
    ('ups',        'UPS Panels',        'ups-panels'),
    ('apfc',       'APFC Panels',       'apfc-panels'),
    ('production', 'Production Panels', 'production-panels'),
    ('utility',    'Utility Panels',    'utility-panels'),
    ('hvac',       'HVAC Panels',       'hvac-panels'),
]

NAV_CATEGORY_ORDER = {cid: i for i, (cid, _label, _slug) in enumerate(NAV_CATEGORIES)}
NAV_CATEGORY_LABEL = {cid: label for cid, label, _slug in NAV_CATEGORIES}
NAV_CATEGORY_SLUG = {cid: slug for cid, _label, slug in NAV_CATEGORIES}
# Reserved by the importer before any group slug — a LoggerFast group literally
# named "HVAC Panels" must not steal the category's slug (backend2 hit exactly
# this class of bug; see backend2/core/views.py:185-196).
NAV_CATEGORY_SLUGS = frozenset(NAV_CATEGORY_SLUG.values())

# --- axis 1: panel_kind -> category -----------------------------------------
# The four structural switchgear categories. 'distribution_panel' is the model
# default and maps to NOTHING — such a row falls through to nav_category.
PANEL_KIND_CATEGORY = {
    'ht_panel':  'ht',
    'amf_panel': 'amf',
    'pcc_panel': 'pcc',
    'ups_panel': 'ups',
}

# --- axis 2: nav_category ----------------------------------------------------
# The ONLY values a GROUP's nav_category may hold; the other four are
# panel_kind's job. (A meter has no panel_kind — see MFM_NAV_CATEGORY_CHOICES.)
NAV_CATEGORY_CHOICES = [
    ('apfc',       'APFC Panels'),
    ('production', 'Production Panels'),
    ('utility',    'Utility Panels'),
    ('hvac',       'HVAC Panels'),
]
NAV_CATEGORY_CODES = frozenset(cid for cid, _label in NAV_CATEGORY_CHOICES)

# '' is the "not filed yet" sentinel — blank, never NULL (see core/models.py).
UNCATEGORISED = ''

NAV_CATEGORY_SOURCE_CHOICES = [
    ('auto',   'Auto — the importer heuristic may reseed it'),
    ('manual', 'Manual — set by hand, importer never touches it'),
]

# A meter has no PanelConfig, so no panel_kind: its nav_category is its ONLY
# axis and may name any of the eight (e.g. a standalone meter under HT Panels).
MFM_NAV_CATEGORY_CHOICES = [(cid, label) for cid, label, _slug in NAV_CATEGORIES]

# The admin's single "Category" dropdown on a panel, written back onto the two
# axes — the exact inverse of what core.nav resolves, so no information is lost.
CATEGORY_PANEL_KIND = {cid: kind for kind, cid in PANEL_KIND_CATEGORY.items()}


def panel_axes(cid):
    """Category id -> ``(panel_kind, nav_category)`` for a panel filed there.

    HT/AMF/PCC/UPS are structural: they set ``panel_kind`` and leave
    ``nav_category`` blank. The other four keep the 'distribution_panel' kind
    and set ``nav_category``.
    """
    kind = CATEGORY_PANEL_KIND.get(cid)
    if kind:
        return kind, UNCATEGORISED
    return 'distribution_panel', cid

# ---------------------------------------------------------------------------
# The classifier.
#
# The LoggerFast naming convention is NOT known to us, so a wrong guess silently
# miscategorises equipment AND LOOKS CORRECT in the sidebar. Hence four rules:
#
#   1. match on the slug the importer already computed (lowercased, punctuation
#      collapsed, deterministic, and the string the FE routes on);
#   2. WHOLE TOKENS, never substrings. This is the key departure from backend2,
#      whose canon() (seed_plant.py:69) strips separators then substring-tests —
#      which is how 'ups' in 'groups', 'dg' in 'edge' and 'ht' in 'light' become
#      silent miscategorisations;
#   3. first informative token wins, after a small stopword skip, so
#      'panel-ahu-3' still reaches 'ahu';
#   4. NO default category. A miss is a 30-second admin dropdown; a
#      wrong-but-plausible bucket is a bug nobody notices for a month.
# ---------------------------------------------------------------------------

# Whole slug tokens that name a placement. Deliberately SMALL: every entry is a
# term that means exactly one thing in switchgear naming.
PANEL_KIND_TOKENS = {
    'ht': 'ht_panel', '11kv': 'ht_panel', '33kv': 'ht_panel',
    'vcb': 'ht_panel', 'switchyard': 'ht_panel', 'crp': 'ht_panel',
    'amf': 'amf_panel',
    'pcc': 'pcc_panel', 'mcc': 'pcc_panel',
    'ups': 'ups_panel', 'mupsdb': 'ups_panel',
}
NAV_CATEGORY_TOKENS = {
    'apfc': 'apfc', 'apfcr': 'apfc', 'capacitor': 'apfc', 'kvar': 'apfc',
    'hhf': 'apfc',
    'utility': 'utility', 'mrpdb': 'utility', 'mldb': 'utility',
    'meldb': 'utility', 'lighting': 'utility', 'fire': 'utility',
    'hsd': 'utility',
    'production': 'production', 'bpdb': 'production', 'pdb': 'production',
    'lamination': 'production', 'laminator': 'production',
    'packaging': 'production', 'flipper': 'production', 'bopt': 'production',
    'hvac': 'hvac', 'ahu': 'hvac', 'chiller': 'hvac', 'csu': 'hvac',
    # 'washer', not 'airwasher': "Air Washer-1" tokenises to [air, washer, 1],
    # so a joined token could never match.
    'washer': 'hvac', 'aw': 'hvac', 'exhaust': 'hvac', 'cda': 'hvac',
    'pcw': 'hvac',
    # NOT mapped, deliberately: 'compressor' / 'dryer'. backend2 files them as
    # BMS/CDA equipment, but the plant outline lists "Air Compressor PDB-1/2"
    # under PRODUCTION Panels. Mapping 'compressor' to hvac would beat the 'pdb'
    # token and file them wrong; leaving them out lets 'pdb' decide correctly.
}

# Tokens whose mapping is a genuine GUESS about this site's conventions. They
# still seed, but every row they decide is listed separately in the import
# report so the operator eyeballs exactly those. Note 'dg' is NOT a token at
# all: backend2 files "11KV DG SYNC Panel" under HT Panels (ems_tree.py:25), not
# AMF, so a DG-named row must be assigned by hand rather than guessed.
# Shrink this set as the naming convention becomes known.
LOW_CONFIDENCE = frozenset({'aw', 'mcc', 'crp', 'hhf', 'kvar',
                            'pdb', 'pcw', 'cda', 'exhaust'})

# Skipped before the lookup so a leading 'panel-' or 'main-' never shadows the
# informative token ('panel-ahu-3' must still reach 'ahu').
STOPWORDS = frozenset({
    'panel', 'panels', 'board', 'db', 'main', 'mains', 'incomer', 'incoming',
    'outgoing', 'outgoer', 'spare', 'feeder', 'lt', 'bus', 'section', 'unit',
    'room', 'new', 'old', 'no', 'module',
})

_TOKENS = re.compile(r'[^a-z0-9]+')
_LEADING_ALPHA = re.compile(r'^([a-z]+)\d+$')


def _split(value):
    """Slug/name -> lowercase alphanumeric tokens."""
    return [t for t in _TOKENS.split(str(value or '').lower()) if t]


def _stem(token):
    """'pcc1' -> 'pcc', 'ahu03' -> 'ahu'. A numeric-leading token like '11kv'
    has no alpha prefix and is left whole."""
    m = _LEADING_ALPHA.match(token)
    return m.group(1) if m else token


def classify(slug, name=''):
    """``(panel_kind, nav_category, matched_token)`` for a row.

    Exactly one of ``panel_kind`` / ``nav_category`` is non-empty; both are ''
    when nothing matched. ``matched_token`` is the token that decided it (''
    on no match) — the import report names it so a wrong guess is diagnosable
    in one line instead of a debugging session.
    """
    # Slug first, then the raw name. The two normally tokenise alike (the slug
    # is derived from the name), but _slug() falls back to the literal 'item'
    # for a name it cannot transliterate — so without the second pass such a
    # row would be unclassifiable even though its name says "AHU Panel 3".
    for source in (slug, name):
        for token in _split(source):
            if token in STOPWORDS:
                continue
            for candidate in (token, _stem(token)):
                kind = PANEL_KIND_TOKENS.get(candidate)
                if kind:
                    return kind, UNCATEGORISED, candidate
                category = NAV_CATEGORY_TOKENS.get(candidate)
                if category:
                    return '', category, candidate
    return '', UNCATEGORISED, ''


def resolve_mfm_category(mfm, memberships=None):
    """The category a LOOSE meter is filed under: its own value when set, else
    the first active plain group it belongs to.

    NEVER persisted. A materialised inherited value is indistinguishable from a
    human override, which would destroy the very signal ``nav_category_source``
    exists to carry, and it would go stale the moment the group is re-filed.
    """
    if mfm.nav_category:
        return mfm.nav_category
    rows = memberships if memberships is not None else (
        mfm.memberships.select_related('group').order_by('position', 'id'))
    for m in rows:
        if m.group.active and m.group.nav_category:
            return m.group.nav_category
    return UNCATEGORISED
