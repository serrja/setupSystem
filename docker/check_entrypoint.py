"""Verify help and read-only planning without a terminal or host system apply."""

import subprocess
import sys
from pathlib import Path

from setup_system.application.installer import InstallationService, InstallRequest, Operation
from setup_system.domain.settings import Section

subprocess.run([sys.executable, "-I", "-m", "setup_system", "--help"], check=True, timeout=30)
root = Path(__file__).resolve().parents[1]
plan = InstallationService(root).plan(InstallRequest(Operation.PLAN, section=Section.WORKSPACE))
assert not plan.actions
assert not plan.blockers
