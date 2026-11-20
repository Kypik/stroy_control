import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [value.strip() for value in os.getenv(name, default).split(',') if value.strip()]


MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
MEDIA_URL = '/media/'

SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = env_bool('DEBUG')
SECURE_MODE = env_bool('SECURE_MODE')
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')
TESSERACT_CMD = os.getenv('TESSERACT_CMD')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.security.AdminLocalOnlyMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'stroy_control.urls'

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
                'core.site_context.site_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'stroy_control.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LANGUAGE_CODE = 'ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

FSA_ALLOW_UNVERIFIED_SSL = env_bool('FSA_ALLOW_UNVERIFIED_SSL', False)

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

# Боевой режим включается при публикации через туннель.
if SECURE_MODE:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # SECURE_SSL_REDIRECT включать только после проверки, что туннель присылает
    # X-Forwarded-Proto: https, иначе получится цикл редиректов.

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'login'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '{levelname} {asctime} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'loggers': {
        'core': {
            'handlers': ['console'],
            'level': 'INFO' if DEBUG else 'WARNING',
            'propagate': False,
        },
    },
}
