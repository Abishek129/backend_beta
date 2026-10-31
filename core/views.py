"""REST views: the nav tree, per-id nameplate config, service status, and the
spreadsheet upload and JSON push that build the tree."""
import base64
import json

import requests
from django.conf import settings
from django.contrib.auth import authenticate
from django.db.models import Max
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.config import resolve_for_id
from core.constants import FEEDER_ID_OFFSET
from core.models import Equipment, Group, MFM, ViewerAsset
from core.nav import build_tree, ungrouped_mfms
from core.nav_push import run_push
from core.sheet_import import SheetError, build_template, read_upload, run_import
from core.viewer import resolve_viewer

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def ems(request):
    """Equipment sidebar tree — CMD_V2 ``EquipmentTreeResponse`` shape."""
    return JsonResponse(build_tree())


@require_GET
def bms(request):
    """Application-shell BMS tree; this EMS service currently owns no BMS nodes."""
    return JsonResponse({'count': 0, 'tree': []})


@require_GET
def assets(request):
    """Application-shell asset tree; asset meters are not populated in beta yet."""
    return JsonResponse({'count': 0, 'tree': []})


@require_GET
def viewer_catalog(request):
    """Active model catalogue used by the shared kit-preview infrastructure."""
    rows = ViewerAsset.objects.filter(active=True).values(
        'slug', 'label', 'url', 'rating', 'defaults')
    assets = list(rows)
    return JsonResponse({'count': len(assets), 'assets': assets})


def mfm_config(request, mfm_id):
    """Nameplate / limits for ws-api id ``mfm_id`` (panel id or offset meter id)."""
    subject, mfm_type, cfg = resolve_for_id(mfm_id)
    if subject is None:
        return JsonResponse({'detail': 'not found'}, status=404)
    # PIN 2026-09-09: panel_id ships the SLUG (beta has no Equipment.key); the
    # config_table name tells the FE which of the two override tables answered.
    equipment = getattr(subject, 'equipment', None) if isinstance(subject, MFM) else None
    panel_id = equipment.key if equipment is not None else subject.slug
    return JsonResponse({
        'mfm_id': mfm_id,
        'panel_id': panel_id,
        'mfm_type': mfm_type,
        'config_table': ('panel_config' if mfm_id < FEEDER_ID_OFFSET else
                         'equipment_config' if equipment is not None else 'mfm_config'),
        'config': cfg,
        'object': (resolve_viewer(equipment, request.GET.get('default', 'panel'))['object']
                   if equipment is not None else None),
    })


@require_GET
def equipment_viewer(request, key):
    equipment = (Equipment.objects.filter(key=key)
                 .select_related('panel_type', 'asset_type', 'config').first())
    if equipment is None:
        return JsonResponse({'detail': 'not found'}, status=404)
    return JsonResponse(resolve_viewer(equipment, request.GET.get('default', 'panel')))


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


def _staff_from_basic_auth(request):
    """The active staff user named by an HTTP Basic header, else None.

    Basic auth only — never the admin session cookie: this view is CSRF-exempt,
    so honouring the session would let any page an admin visits post uploads.
    """
    scheme, _, encoded = request.META.get('HTTP_AUTHORIZATION', '').partition(' ')
    if scheme.lower() != 'basic' or not encoded:
        return None
    try:
        username, _, password = base64.b64decode(encoded).decode('utf-8').partition(':')
    except (ValueError, UnicodeDecodeError):
        return None
    user = authenticate(request, username=username, password=password)
    return user if user is not None and user.is_active and user.is_staff else None


def _unauthorised():
    response = JsonResponse({'detail': 'Admin credentials required (HTTP Basic auth).'},
                            status=401)
    response['WWW-Authenticate'] = 'Basic realm="EMS import"'
    return response


def _commit_requested(request):
    return (request.GET.get('commit') or request.POST.get('commit') or '').lower() \
        in ('1', 'true', 'yes')


@csrf_exempt
@require_POST
def ems_import(request):
    """Upload a filled template (form field ``file``, .xlsx or .csv).

    A preview by default — reports what would change and every row error, and
    writes nothing. ``commit=true`` writes, all or nothing.
    """
    if _staff_from_basic_auth(request) is None:
        return _unauthorised()
    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'detail': 'Attach the sheet as form field "file".'}, status=400)
    if upload.size > _MAX_UPLOAD_BYTES:
        return JsonResponse({'detail': 'File is larger than 10 MB.'}, status=413)
    commit = _commit_requested(request)
    try:
        sheets = read_upload(upload.name, upload.read())
    except SheetError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)
    report = run_import(sheets, commit=commit)
    return JsonResponse(report.as_dict(), status=200 if report.ok else 400)


@csrf_exempt
@require_POST
def ems_push(request):
    """Replace the whole EMS sidebar from a JSON body (see ``core.nav_push``).

    A preview by default — reports every change and error and returns the nav
    the push would produce, writing nothing. ``commit=true`` writes, all or
    nothing.
    """
    if _staff_from_basic_auth(request) is None:
        return _unauthorised()
    try:
        body = json.loads(request.body or b'null')
    except ValueError:
        return JsonResponse({'detail': 'The body must be JSON.'}, status=400)
    report = run_push(body, commit=_commit_requested(request))
    return JsonResponse(report.as_dict(), status=200 if report.ok else 400)


@require_GET
def ems_import_template(request):
    """The empty import template (.xlsx) — the format ``ems_import`` reads."""
    response = HttpResponse(build_template(), content_type=_XLSX)
    response['Content-Disposition'] = 'attachment; filename="ems_import_template.xlsx"'
    return response


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
        'ungrouped_mfms': ungrouped_mfms().count(),
        'last_import_at': last_import.isoformat() if last_import else None,
        'version': 'beta',
    })
