"""REST views: the nav tree, per-id nameplate config, and service status."""
import requests
from django.conf import settings
from django.db.models import Max
from django.http import JsonResponse

from core.config import resolve_for_id
from core.constants import FEEDER_ID_OFFSET
from core.models import Group, MFM
from core.nav import build_tree


def ems(request):
    """Equipment sidebar tree — CMD_V2 ``EquipmentTreeResponse`` shape."""
    return JsonResponse(build_tree())


def mfm_config(request, mfm_id):
    """Nameplate / limits for ws-api id ``mfm_id`` (panel id or offset meter id)."""
    subject, mfm_type, cfg = resolve_for_id(mfm_id)
    if subject is None:
        return JsonResponse({'detail': 'not found'}, status=404)
    # PIN 2026-09-09: panel_id ships the SLUG (beta has no Equipment.key); the
    # config_table name tells the FE which of the two override tables answered.
    return JsonResponse({
        'mfm_id': mfm_id,
        'panel_id': subject.slug,
        'mfm_type': mfm_type,
        'config_table': 'panel_config' if mfm_id < FEEDER_ID_OFFSET else 'mfm_config',
        'config': cfg,
        'object': None,          # 3D resolution is out of scope in beta
    })


def _logger_api_reachable():
    """True when the LoggerFast API answers; never raises, 2 s budget."""
    base = (getattr(settings, 'LOGGER_API_URL', '') or '').rstrip('/')
    if not base:
        return False
    for path in ('/health', '/'):
        try:
            if requests.get(base + path, timeout=2).status_code < 500:
                return True
        except Exception:
            continue
    return False


def status(request):
    """Service health + import counters (what the operator checks after a run)."""
    bound = (MFM.objects.filter(active=True, data_source__isnull=False)
             .exclude(table_name='').count())
    last_import = Group.objects.aggregate(v=Max('imported_at'))['v']
    return JsonResponse({
        'db': True,
        'logger_api_reachable': _logger_api_reachable(),
        'groups': Group.objects.filter(active=True).count(),
        'panels': Group.objects.filter(active=True, is_panel=True).count(),
        'mfms': MFM.objects.filter(active=True).count(),
        'bound_mfms': bound,
        'ungrouped_mfms': MFM.objects.filter(active=True, memberships__isnull=True).count(),
        'last_import_at': last_import.isoformat() if last_import else None,
        'version': 'beta',
    })
