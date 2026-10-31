"""REST routes — mounted under ``/api/`` by config/urls.py."""
from django.urls import path

from core.views import (assets, bms, ems, ems_import, ems_import_template, ems_push,
                        equipment_viewer, mfm_config, status, viewer_catalog)

urlpatterns = [
    path('ems/', ems, name='ems'),
    path('ems/push/', ems_push, name='ems-push'),
    path('bms/', bms, name='bms'),
    path('assets/', assets, name='assets'),
    path('kit-preview/catalog/', viewer_catalog, name='viewer-catalog'),
    path('import/', ems_import, name='ems-import'),
    path('import/template/', ems_import_template, name='ems-import-template'),
    path('mfm/<int:mfm_id>/config/', mfm_config, name='mfm-config'),
    path('equipment/key/<str:key>/viewer/', equipment_viewer, name='equipment-viewer'),
    path('status/', status, name='status'),
]
