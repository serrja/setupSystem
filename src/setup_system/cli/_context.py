"""Resolve data in a checkout or an installed wheel without side effects."""

from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = SOURCE_ROOT if (SOURCE_ROOT / "manifests/platforms.toml").is_file() else PACKAGE_ROOT
