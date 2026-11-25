"""Exercise native parameter transfer with an unprivileged local Ansible play."""

import base64
import json
import os
import shutil
import subprocess

import pytest

from setup_system_sdk.native_play import public_parameters


def test_parameters_remain_literal_in_native_ansible(tmp_path):
    args = {"setting": "Literal {{ 3 * 4 }} and Пример"}
    option = public_parameters(args)
    assert "{{" not in option
    assert json.loads(base64.b64decode(option.split("=", 1)[1])) == args
    with pytest.raises(ValueError):
        public_parameters({"setting": "a" * 65537})
    script = tmp_path / "read.yml"
    script.write_text(
        "- hosts: localhost\n  gather_facts: false\n  tasks:\n"
        "    - ansible.builtin.raw: >-\n"
        "        /usr/bin/printf %s {{ setup_system_payload }} | /usr/bin/base64 --decode\n"
        "      register: observed\n      changed_when: false\n"
        "    - ansible.builtin.assert:\n        that: observed.rc == 0\n"
    )
    executable = shutil.which("ansible-playbook")
    assert executable is not None
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "ANSIBLE_HOME": str(tmp_path / "ansible"),
    }
    result = subprocess.run(
        [executable, "-i", "localhost,", "-c", "local", "-vv", "-e", option, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert b"{{ 3 * 4 }}" in result.stdout
