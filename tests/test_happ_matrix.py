"""The published matrix supplies the only reviewed service manager release."""

import subprocess
from pathlib import Path

from setup_system.adapters.debian.happ_service import validate_service_manager


def test_service_manager_checks_the_version_in_the_selected_matrix(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "manifests/platforms.toml"
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    policy = source.read_text()
    import re

    (manifests / "platforms.toml").write_text(
        re.sub(r'^init_version = "[^"]+"$', 'init_version = "258"', policy, flags=re.MULTILINE)
    )

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        value = "systemd 258 (258.1)" if "--version" in argv else "258.1"
        return subprocess.CompletedProcess(argv, 0, value, "")

    validate_service_manager(root=tmp_path, run=run)
