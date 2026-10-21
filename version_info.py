"""Read the version embedded in installed builds or resolved from source tags."""

from pathlib import Path
import sys


def application_version():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    if not getattr(sys, "frozen", False):
        try:
            from scripts.release import current_version
            return current_version(root)
        except (OSError, ValueError):
            pass
    try:
        from scripts.release import parse_version
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        parse_version(version)
        return version
    except (OSError, ValueError):
        return "0.0.0"
