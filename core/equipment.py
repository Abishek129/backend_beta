"""Create the additive equipment metadata that accompanies Groups and MFMs."""
from core.models import (AssetType, Breaker, Equipment, EquipmentConfig,
                         MfmThreshold, PanelType)


PANEL_TYPES = {
    'distribution_panel': 'Distribution Panel', 'pcc_panel': 'PCC Panel',
    'ht_panel': 'HT Panel', 'amf_panel': 'AMF Panel', 'ups_panel': 'UPS Panel',
    'lt_panel': 'LT Panel', 'transformer': 'Transformer',
}
ASSET_TYPES = {'dg': 'Diesel Generator', 'ups': 'UPS', 'solar': 'Solar'}


def ensure_types():
    panels = {code: PanelType.objects.get_or_create(code=code, defaults={'name': name})[0]
              for code, name in PANEL_TYPES.items()}
    assets = {code: AssetType.objects.get_or_create(code=code, defaults={'name': name})[0]
              for code, name in ASSET_TYPES.items()}
    return panels, assets


def _free_key(base):
    candidate, n = base, 2
    while Equipment.objects.filter(key=candidate).exists():
        candidate, n = f'{base}-{n}', n + 1
    return candidate


def ensure_panel_equipment(group):
    """One canonical Equipment row for a distribution-panel Group."""
    panels, _assets = ensure_types()
    cfg = getattr(group, 'config', None)
    kind = getattr(cfg, 'panel_kind', None) or 'distribution_panel'
    if hasattr(group, 'equipment'):
        row = group.equipment
        wanted_type = panels.get(kind, panels['distribution_panel'])
        changed = []
        for field, value in (('name', group.name), ('panel_type', wanted_type),
                             ('asset_type', None), ('distribution_panel', True)):
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(field)
        if changed:
            row.save(update_fields=changed + ['updated_at'])
        EquipmentConfig.objects.get_or_create(equipment=row)
        return row
    row = Equipment.objects.create(
        key=_free_key(group.slug), name=group.name, group=group,
        panel_type=panels.get(kind, panels['distribution_panel']),
        distribution_panel=True, metered=group.memberships.exists())
    EquipmentConfig.objects.get_or_create(equipment=row)
    return row


def ensure_mfm_equipment(mfm):
    """One measured Equipment row per MFM unless an operator mapped one."""
    panels, assets = ensure_types()
    cfg = getattr(mfm, 'config', None)
    kind = getattr(cfg, 'device_type', None) or 'lt_panel'
    if mfm.equipment_id:
        row, update = mfm.equipment, []
    else:
        fields = ({'asset_type': assets[kind]} if kind in assets
                  else {'panel_type': panels.get(kind, panels['lt_panel'])})
        row = Equipment.objects.create(
            key=_free_key(mfm.slug), name=mfm.name, metered=True,
            distribution_panel=False, **fields)
        mfm.equipment = row
        update = ['equipment']
    direct = {
        'rated_capacity_kva': getattr(cfg, 'rated_kva', None),
        'nominal_voltage_v': getattr(cfg, 'nominal_voltage_v', None),
        'nameplate_voltage_kv': getattr(cfg, 'nameplate_voltage_kv', None),
        'frequency_hz': getattr(cfg, 'frequency_hz', None),
        'fault_level_ka': getattr(cfg, 'fault_level_ka', None),
    }
    for field, value in direct.items():
        if getattr(mfm, field) is None and value is not None:
            setattr(mfm, field, value)
            update.append(field)
    if update:
        mfm.save(update_fields=update)
    MfmThreshold.objects.get_or_create(
        mfm=mfm,
        defaults={'i_thd_limit_pct': getattr(cfg, 'thd_i_limit_pct', None) or 8.0,
                  'v_thd_limit_pct': getattr(cfg, 'thd_v_limit_pct', None) or 5.0,
                  'individual_harmonic_limit_pct': 6.0,
                  'flicker_pst_limit': 1.0, 'crest_factor_ideal': 1.414,
                  'overview_freq_nominal_hz': getattr(cfg, 'frequency_hz', None) or 50.0})
    Breaker.objects.get_or_create(
        mfm=mfm,
        defaults={'breaker_type': 'VCB' if 'VCB' in mfm.name.upper() else 'unknown',
                  'rating_a': getattr(cfg, 'rated_current_a', None),
                  'source': 'automatic'})
    return row


def ensure_primary_reference(mfm):
    """Fill the compatibility location without collapsing multi-panel membership."""
    if mfm.reference_id:
        return mfm.reference
    membership = (mfm.memberships.filter(group__is_panel=True)
                  .select_related('group').order_by('position', 'id').first())
    if membership is not None:
        mfm.reference = membership.group
        mfm.save(update_fields=['reference'])
        Breaker.objects.filter(mfm=mfm, panel_key='').update(panel_key=membership.group.slug)
        return membership.group
    return None
