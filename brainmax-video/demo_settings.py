from pathlib import Path

from BrainMax.settings import *


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path("/tmp/brainmax-video-demo.sqlite3"),
    }
}
CHROMA_DB_PATH = Path("/tmp/brainmax-video-demo-chroma")
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
