UV ?= uv
PLAYBOOKS := $(wildcard ansible/*.yml)
SHELL_SOURCES := debian/publish $(wildcard *.sh assets/hyprland/*.sh debian/*.sh docker/*.sh libexec/*.sh)

.PHONY: lint typecheck test native-check native-build native-verify native-ci
lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked ruff format --check --line-length 88 docker/release.py docker/python_release.py
	$(UV) run --locked shellcheck $(SHELL_SOURCES)
	$(UV) run --locked yamllint -d '{rules: {key-duplicates: enable}}' .github ansible
	ANSIBLE_HOME="$(CURDIR)/coverage/ansible" $(UV) run --locked ansible-lint --offline ansible/
	ANSIBLE_HOME="$(CURDIR)/coverage/ansible" $(UV) run --locked ansible-playbook --syntax-check -i localhost, $(PLAYBOOKS)
typecheck:
	$(UV) run --locked mypy
	$(UV) run --locked mypy --python-version 3.12 --no-incremental docker/python_release.py
test:
	umask 022 && env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR $(UV) run --locked pytest --cov --cov-report=json --cov-report=xml --cov-report=term-missing --junitxml=coverage/python/junit.xml
native-check: lint typecheck test
	$(UV) lock --check
	$(UV) run --locked setup-system --help
native-build:
	$(UV) build --no-build-isolation --wheel --sdist --out-dir coverage/native-artifacts
native-verify:
	$(UV) run --locked python docker/verify_native.py coverage/native-artifacts
native-ci: native-check native-build native-verify package
