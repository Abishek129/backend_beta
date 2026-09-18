"""ASGI config — HTTP (Django) + WebSocket (Channels)."""
import faulthandler
import os
import signal

# kill -USR1 <pid> dumps every thread's stack to stderr (journal) —
# on-demand diagnosis for event-loop / executor stalls without a debugger.
faulthandler.register(signal.SIGUSR1, all_threads=True)

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialise Django before importing anything that touches models/consumers.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402

from assets.routing import websocket_urlpatterns as asset_ws  # noqa: E402
from bms.routing import websocket_urlpatterns as bms_ws  # noqa: E402
from panels.routing import websocket_urlpatterns as panel_ws  # noqa: E402

# Wrap HTTP so Daphne serves admin/jazzmin static (CSS/JS) in DEBUG.
# PIN 2026-09-09: assets/bms route lists are empty (no consumers yet); panels
# carries only the invalid-id catch-all until screens are registered.
application = ProtocolTypeRouter({
    'http': ASGIStaticFilesHandler(django_asgi_app),
    'websocket': URLRouter(panel_ws + asset_ws + bms_ws),
})
