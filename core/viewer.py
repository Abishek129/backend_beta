"""Equipment-key 3D viewer rule resolution."""
from copy import deepcopy

from core.models import Equipment, ViewerAsset, ViewerRule, ViewerSetting


def _merge(base, overlay):
    out = deepcopy(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def equipment_type(equipment):
    return (getattr(equipment.panel_type, 'code', None)
            or getattr(equipment.asset_type, 'code', None)
            or 'equipment')


def resolve_viewer(equipment: Equipment, fallback='panel'):
    kind = equipment_type(equipment)
    config = getattr(equipment, 'config', None)
    rating = getattr(config, 'rating', '') or ''
    rules = (ViewerRule.objects.filter(active=True, asset__active=True)
             .select_related('asset'))
    rule = rules.filter(for_key=equipment.key).first()
    if rule is None and rating:
        rule = rules.filter(for_key='', for_type=kind, rating=rating).first()
    if rule is None:
        rule = rules.filter(for_key='', for_type=kind, rating='').first()

    asset = rule.asset if rule else None
    if asset is None:
        setting = ViewerSetting.objects.filter(key=f'default_{fallback}_asset').first()
        slug = setting.value.get('slug') if setting and isinstance(setting.value, dict) else None
        if slug:
            asset = ViewerAsset.objects.filter(slug=slug, active=True).first()
    global_row = ViewerSetting.objects.filter(key='viewer_defaults').first()
    global_defaults = global_row.value if global_row and isinstance(global_row.value, dict) else {}
    viewer = _merge(global_defaults, asset.defaults if asset else {})
    viewer = _merge(viewer, rule.preset if rule else {})
    return {
        'equipment': {
            'id': equipment.id, 'key': equipment.key,
            'kind': 'asset' if equipment.asset_type_id else 'panel',
            'type': kind, 'pageType': kind,
        },
        'object': ({
            'slug': asset.slug, 'label': asset.label, 'url': asset.url,
            'rating': asset.rating or None,
        } if asset else None),
        'viewer': viewer,
    }
