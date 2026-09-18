"""Django settings for backend_beta.

Derived from backend2's config/settings.py, minus assets/bms/kit_preview and with
the LoggerFast import knobs added. Env comes from a `.env` beside manage.py
(python-dotenv) so the same file works on archbox and on the plant server.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# PIN 2026-09-09: load .env before any os.environ read below; real env wins.
load_dotenv(BASE_DIR / '.env', override=False)


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-beta-8wv%_1$kmkb#zkk@%2&)fzovlq&c68u3)_e$6dsw3$sjza1(h&')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'

# Comma-separated env override (DJANGO_ALLOWED_HOSTS); default '*' = dev behaviour.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',') if h.strip()]


# Application definition

INSTALLED_APPS = [
    'daphne',
    'jazzmin',  # must precede django.contrib.admin to override its templates
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'corsheaders',
    'rest_framework',
    'core',
    'panels',
    # PIN 2026-09-09: registered for structure (mirrors backend2); no models/consumers yet.
    'assets',
    'bms',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',  # must precede CommonMiddleware
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}

# CORS — open for dev / internal Tailscale + LAN access so the CMD_V2 frontend
# (served from a different origin/port) can call the REST API from the browser.
# DJANGO_CORS_ORIGINS (comma-separated) tightens CORS in prod; unset = open.
_cors_origins = [o.strip() for o in os.environ.get('DJANGO_CORS_ORIGINS', '').split(',') if o.strip()]
if _cors_origins:
    CORS_ALLOWED_ORIGINS = _cors_origins
    CORS_ALLOW_ALL_ORIGINS = False
else:
    CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'cmd_equipment_beta',
        'USER': 'postgres',
        'PASSWORD': '',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Kolkata'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'
# collectstatic target — needed to gather admin/jazzmin assets when DEBUG=False.
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- LoggerFast import source -------------------------------------------------
# The hierarchy importer reads the LoggerFast HTTP API; the sockets read its
# timeseries DB directly through LOGGER_TIMESERIES_DSN (one DataSource row).
LOGGER_API_URL = os.environ.get('LOGGER_API_URL', 'http://127.0.0.1:8003')
LOGGER_API_USER = os.environ.get('LOGGER_API_USER', '')
LOGGER_API_PASSWORD = os.environ.get('LOGGER_API_PASSWORD', '')
LOGGER_API_TIMEOUT = float(os.environ.get('LOGGER_API_TIMEOUT', '20'))
LOGGER_TIMESERIES_DSN = os.environ.get(
    'LOGGER_TIMESERIES_DSN',
    'postgresql://postgres@127.0.0.1:5433/target_version1?options=-csearch_path%3Dneuract')

# Substring that marks a DSN as the REAL logger (services.py applies its data
# corrections only there). services.py reads it from os.environ, so mirror it.
CMD_REAL_DSN_MARKER = os.environ.get('CMD_REAL_DSN_MARKER', 'target_version1')
os.environ.setdefault('CMD_REAL_DSN_MARKER', CMD_REAL_DSN_MARKER)


# Jazzmin admin theme (https://django-jazzmin.readthedocs.io/configuration/)
JAZZMIN_SETTINGS = {
    'site_title': 'CMD Beta Admin',
    'site_header': 'CMD Beta Admin',
    'site_brand': 'CMD Beta Admin',
    'welcome_sign': 'Welcome to CMD Beta Admin',
    'copyright': 'Neuract',
    'search_model': ['core.Group', 'core.MFM'],
    'icons': {
        'auth.User': 'fas fa-user',
        'auth.Group': 'fas fa-users',
        'core.Group': 'fas fa-layer-group',
        'core.MFM': 'fas fa-gauge-high',
        'core.DataSource': 'fas fa-database',
        'core.Membership': 'fas fa-diagram-project',
        'core.FeedEdge': 'fas fa-arrow-right-long',
        'core.PanelConfig': 'fas fa-sliders',
        'core.MfmConfig': 'fas fa-sliders',
        'core.RtmThreshold': 'fas fa-traffic-light',
    },
    'order_with_respect_to': ['core', 'panels', 'auth'],
    'default_icon_parents': 'fas fa-chevron-circle-right',
    'default_icon_children': 'fas fa-circle',
    'show_ui_builder': False,
}

JAZZMIN_UI_TWEAKS = {
    'theme': 'default',
    'navbar': 'navbar-white navbar-light',
    'sidebar': 'sidebar-dark-primary',
}

# --- AI summary service (graph_summary, separate process) --------------------
# Sockets POST a compact view payload here on connect / param-change and push the
# returned one-paragraph summary as an `ai` frame. See core/ai_client.py +
# core/ai_frame.py. The service itself fans out to the LLM (vLLM :8200).
AI_SUMMARY_URL = os.getenv('AI_SUMMARY_URL', 'http://127.0.0.1:8077')
AI_SUMMARY_TIMEOUT = float(os.getenv('AI_SUMMARY_TIMEOUT', '8'))
AI_SUMMARY_ENABLED = os.getenv('AI_SUMMARY_ENABLED', '0') not in ('0', 'false', 'False', '')
# Cap concurrent in-flight summary calls so a burst of socket connects doesn't
# flood the LLM; excess calls queue (the AI run is a background task, so waiting
# never blocks the data frames).
AI_SUMMARY_CONCURRENCY = int(os.getenv('AI_SUMMARY_CONCURRENCY', '3'))

# Admin grid-edit (list_editable) bulk-saves post one field per cell; a wide
# threshold grid (20 editable cols) over a full page exceeds Django's default
# DATA_UPLOAD_MAX_NUMBER_FIELDS (1000). Raise it so bulk Save works.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000
