"""Read-only HTTP client for the LoggerFast API — the hierarchy import source.

Only ``/groups*`` and ``/device_edges`` need a token; ``/devices`` and
``/tables`` are public routers. Every method returns the plain ``items`` list.
"""
from __future__ import annotations

import time

import requests

# Re-login this early so a call can never race the 300 s expiry.
_TOKEN_SKEW_S = 30.0
_PAGE_SIZE = 500
_MAX_PAGES = 1000
_BODY_SNIPPET = 300


class LoggerApiError(Exception):
    """A LoggerFast call failed — transport, HTTP status or malformed body."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _snippet(response):
    try:
        return (response.text or '')[:_BODY_SNIPPET]
    except Exception:                                   # pragma: no cover - defensive
        return ''


class LoggerClient:
    """Five read calls against one LoggerFast instance."""

    def __init__(self, base_url, username=None, password=None, timeout=15.0, session=None):
        self.base_url = (base_url or '').rstrip('/')
        self.username = username or ''
        self.password = password or ''
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self._access_token = None
        self._expires_at = 0.0

    # --- auth ---------------------------------------------------------------
    def _login(self):
        """POST /auth/login -> access token; caches the expiry (minus skew)."""
        if not self.username or not self.password:
            raise LoggerApiError(
                'LoggerFast credentials are required for /groups and /device_edges '
                '(set LOGGER_API_USER / LOGGER_API_PASSWORD or pass --user/--password).')
        url = f'{self.base_url}/auth/login'
        try:
            resp = self.session.post(
                url, json={'username': self.username, 'password': self.password},
                timeout=self.timeout)
        except requests.RequestException as exc:
            raise LoggerApiError(f'POST {url} failed: {exc}') from None
        if resp.status_code != 200:
            raise LoggerApiError(f'POST {url} -> HTTP {resp.status_code}',
                                 status=resp.status_code, body=_snippet(resp))
        try:
            data = resp.json()
        except ValueError:
            raise LoggerApiError(f'POST {url} returned a non-JSON body',
                                 status=resp.status_code, body=_snippet(resp)) from None
        token = data.get('access_token')
        if not token:
            raise LoggerApiError(f'POST {url} returned no access_token',
                                 status=resp.status_code, body=_snippet(resp))
        expires_in = float(data.get('expires_in') or 300)
        self._access_token = token
        self._expires_at = time.monotonic() + expires_in - _TOKEN_SKEW_S
        return token

    def _token(self):
        """The cached token, re-logging in when missing or (nearly) expired."""
        if not self._access_token or time.monotonic() >= self._expires_at:
            return self._login()
        return self._access_token

    # --- transport ----------------------------------------------------------
    def _raw_get(self, url, params, headers):
        try:
            return self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise LoggerApiError(f'GET {url} failed: {exc}') from None

    def _get(self, path, params=None, auth=False):
        """One GET; on 401 with ``auth`` re-login once and retry."""
        url = f'{self.base_url}{path}'
        headers = {'Accept': 'application/json'}
        if auth:
            headers['Authorization'] = f'Bearer {self._token()}'
        resp = self._raw_get(url, params, headers)
        if auth and resp.status_code == 401:
            self._access_token = None
            headers['Authorization'] = f'Bearer {self._token()}'
            resp = self._raw_get(url, params, headers)
        if resp.status_code != 200:
            raise LoggerApiError(f'GET {url} -> HTTP {resp.status_code}',
                                 status=resp.status_code, body=_snippet(resp))
        try:
            return resp.json()
        except ValueError:
            raise LoggerApiError(f'GET {url} returned a non-JSON body',
                                 status=resp.status_code, body=_snippet(resp)) from None

    @staticmethod
    def _items(payload, path):
        items = (payload or {}).get('items')
        if not isinstance(items, list):
            raise LoggerApiError(f'{path} returned no "items" list', body=str(payload)[:_BODY_SNIPPET])
        return items

    # --- the five read calls ------------------------------------------------
    def devices(self):
        """GET /devices (public)."""
        return self._items(self._get('/devices'), '/devices')

    def tables(self, status='migrated'):
        """GET /tables (public), paged.

        PIN 2026-09-09: page until a short page — the router's ``total`` counts
        pre-filter rows and cannot be trusted as a stop condition.
        """
        out = []
        for page in range(1, _MAX_PAGES + 1):
            params = {'page': page, 'pageSize': _PAGE_SIZE}
            if status:
                params['status'] = status
            items = self._items(self._get('/tables', params=params), '/tables')
            out.extend(items)
            if len(items) < _PAGE_SIZE:
                break
        return out

    def groups_tree(self):
        """GET /groups/tree (needs logger-read)."""
        return self._items(self._get('/groups/tree', auth=True), '/groups/tree')

    def group_twins(self, gid):
        """GET /groups/{gid}/twins (needs logger-read)."""
        path = f'/groups/{gid}/twins'
        return self._items(self._get(path, auth=True), path)

    def device_edges(self):
        """GET /device_edges (needs logger-read)."""
        return self._items(self._get('/device_edges', auth=True), '/device_edges')
