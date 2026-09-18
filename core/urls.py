"""REST routes — mounted under ``/api/`` by config/urls.py."""
from django.urls import path

from core.views import ems, mfm_config, status

urlpatterns = [
    path('ems/', ems, name='ems'),
    path('mfm/<int:mfm_id>/config/', mfm_config, name='mfm-config'),
    path('status/', status, name='status'),
]
