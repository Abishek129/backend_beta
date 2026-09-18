"""EMS websocket routing — the shared ``ws/mfm/{id}/{screen}/`` namespace.

Routes are GENERATED from ``panels.registry.SCREENS``: one ``type_dispatch``
route per registered screen, then the catch-all. Import the consumer modules
here (their import side effect calls ``screen(...)``) to add routes.

PIN 2026-09-09: no consumer modules imported yet — the list is the catch-all only,
so every ``ws/mfm/...`` connect answers with a 4404 error frame.
"""
from django.urls import path

from panels.dispatch import reject_invalid_id, type_dispatch
from panels.registry import SCREENS

websocket_urlpatterns = [
    path(f'ws/mfm/<int:eid>/{name}/', type_dispatch(**types))
    for name, types in SCREENS.items()
] + [
    # Catch-all (MUST be last): a non-numeric id ('asset-transformers') can't match
    # any <int:eid> route above -> the URLRouter would raise "No route found" -> 500.
    # This rejects cleanly (accept -> error frame -> close 4404) instead. Also catches
    # a valid id with an unknown screen. Int ids with a known screen match above first.
    path('ws/mfm/<str:eid>/<str:screen>/', reject_invalid_id()),
]
