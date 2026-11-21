"""Load the isolated system validator from the built sdist."""

import json
import sys
import tarfile
import tempfile
from pathlib import Path

if not sys.flags.no_site or not sys.flags.isolated or not sys.flags.dont_write_bytecode:
    raise RuntimeError("Use isolated Python with site initialization and bytecode disabled")
with tempfile.TemporaryDirectory(prefix="system-contracts-") as temporary:
    directory = Path(temporary)
    with tarfile.open(sys.argv[1], mode="r:gz") as release:
        release.extractall(directory, filter="data")
    (source,) = directory.iterdir()
    sys.path.insert(0, str(source / "src"))
    from setup_system.bootstrap.validation_runtime import validation_runtime

    with validation_runtime(source):
        import pydantic
        import pydantic_core

        from setup_system.domain.system_commands import COMMAND
        from setup_system.infrastructure import (
            cleanup,
            graphical_unlock,
            privileged_tasks,
            regional,
        )
        from setup_system.infrastructure.admin_editor import editor_ready
        from setup_system.infrastructure.catalog_repository import load_cleanup, load_components

        # Import each executed branch with exactly the deployed root dependency set.
        adapters = (
            cleanup.apply_cleanup,
            graphical_unlock.apply_unlock,
            regional.apply,
            editor_ready,
        )
        assert all(callable(adapter) for adapter in adapters)
        assert load_cleanup(source) and load_components(source).entries
        assert Path(pydantic.__file__).is_relative_to(directory)
        assert Path(pydantic_core.__file__).is_relative_to(directory)
        assert Path(privileged_tasks.__file__).is_relative_to(source)
        assert not any("site-packages" in p for p in sys.path)
        assert not {"ansible_runner", "sitecustomize", "usercustomize"} & sys.modules.keys()
        payload = {"action": "workspace_packages", "settings": {"packages": ["git"]}}
        command = COMMAND.validate_json(json.dumps(payload))
        assert command.action == "workspace_packages" and command.settings.packages == ("git",)
        print(
            f"Isolated contracts passed on CPython {sys.version.split()[0]} "
            f"and Pydantic {pydantic.__version__}."
        )
