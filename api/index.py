import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roadrunner.settings")

from roadrunner.wsgi import application

app = application
