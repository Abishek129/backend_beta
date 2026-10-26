"""Slugs for rows built by hand (admin forms, sheet upload).

The DB constraints are per table, but the FE resolves slugs tree-wide (and the
LoggerFast importer allocates them that way), so a panel and a meter must not
share one, and neither may take a category's.
"""
import re

from core.models import Group, MFM
from core.nav_categories import NAV_CATEGORY_SLUGS


def slugify(value):
    """The importer's slug rule, so hand-made and imported slugs look alike."""
    return re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')[:100] or 'item'


def slug_owner(slug, instance=None):
    """Who else already holds ``slug`` — a category, or an active group/meter —
    as a human-readable phrase; None when it is free. ``instance`` (the row
    being saved) never counts against itself."""
    if slug in NAV_CATEGORY_SLUGS:
        return 'an EMS category'
    for model, kind in ((Group, 'panel/group'), (MFM, 'MFM')):
        qs = model.objects.filter(active=True, slug=slug)
        if isinstance(instance, model) and instance.pk:
            qs = qs.exclude(pk=instance.pk)
        other = qs.first()
        if other is not None:
            return f'{kind} “{other.name}”'
    return None


def free_slug(name, instance=None):
    """``slugify(name)``, suffixed -2, -3, … until nobody holds it."""
    base = slugify(name)
    slug, i = base, 2
    while slug_owner(slug, instance):
        slug, i = f'{base}-{i}', i + 1
    return slug
