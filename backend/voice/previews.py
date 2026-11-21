import re
from pathlib import Path

PREVIEW_DIR = Path(__file__).with_name("previews")
PREVIEW_URL = "/api/voice/previews"


def preview_path(slug: str) -> Path | None:
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        return PREVIEW_DIR / f"{slug}.wav"
    return None
