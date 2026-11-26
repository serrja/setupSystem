from pathlib import Path

from setup_system_sdk.files.contracts import ContractModel


class InspectionPaths(ContractModel):
    zoneinfo: Path = Path("/usr/share/zoneinfo")
    timezone: Path = Path("/etc/timezone")
    localtime: Path = Path("/etc/localtime")
