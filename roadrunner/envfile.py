import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Minimal .env loader, no third-party dependency.

    Reads lines of the form ``KEY=value``; blank lines and lines starting with
    ``#`` are skipped (a ``#`` inside a value is preserved). Uses
    ``os.environ.setdefault`` so a variable already present in the real
    environment (e.g. set in the Vercel dashboard) always wins over the file.
    """
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())
