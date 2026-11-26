"""Load an approved, dependency-light UI module once per application process."""

import base64
import hashlib
import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from importlib.metadata import distributions
from pathlib import Path
from types import CodeType

from setup_system.services.extensions import Activation
from setup_system_sdk import UI_GROUP
from setup_system_sdk.ui import PluginPanel


class _VerifiedUI(SourceFileLoader):
    """Compile the already authenticated UI bytes without writing into the sealed environment."""

    def __init__(self, name: str, path: Path, source: bytes) -> None:
        super().__init__(name, str(path))
        self.source = source

    def get_code(self, fullname: str) -> CodeType:
        return self.source_to_code(self.source, self.path)


def panel_type(state: Activation, startup_digest: str | None) -> type[PluginPanel] | None:
    for distribution in distributions(path=[str(state.resources.parent)]):
        points = distribution.entry_points.select(group=UI_GROUP, name=state.manifest.id)
        if not points:
            continue
        if startup_digest != state.digest:
            raise ValueError("Restart setup-system to load the activated UI version")
        if len(points) != 1 or "ui-code" not in state.manifest.permissions:
            raise ValueError("The extension has no approved host UI declaration")
        point = next(iter(points))
        package = state.manifest.entry_point.partition(":")[0].partition(".")[0]
        parts = point.module.split(".")
        if len(parts) != 2 or parts[0] != package or not all(p.isidentifier() for p in parts):
            raise ValueError("UI must be a separate thin module in the plugin package")
        relative = parts[0] + "/" + parts[1] + ".py"
        file = next((p for p in distribution.files or () if str(p) == relative), None)
        if file is None or not file.hash or file.hash.mode != "sha256":
            raise ValueError("Missing UI distribution fingerprint")
        path = Path(str(distribution.locate_file(file)))
        if path.is_symlink() or path.stat().st_size > 256 * 1024:
            raise ValueError("Invalid plugin UI source")
        source = path.read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(source).digest()).rstrip(b"=").decode()
        if digest != file.hash.value:
            raise ValueError("UI differs from its installed wheel")
        name = "_setup_system_ui_" + state.manifest.id.replace("-", "_") + "_" + state.digest
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(
                name, path, loader=_VerifiedUI(name, path, source)
            )
            if not spec or not spec.loader:
                raise ValueError("UI module is unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(name, None)
                raise ValueError("The UI requires unavailable host dependencies") from None
        component = getattr(module, point.attr or "", None)
        if not isinstance(component, type) or not issubclass(component, PluginPanel):
            raise ValueError("UI component does not implement the supported SDK")
        return component
    return None
