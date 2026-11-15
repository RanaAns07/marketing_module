from config.settings import base as _base

for _k, _v in vars(_base).items():
    if not _k.startswith("_"):
        globals()[_k] = _v

DEBUG = True
ALLOWED_HOSTS = ["*"]
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
