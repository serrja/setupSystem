UV ?= uv
PLUGIN_SOURCES := $(sort $(wildcard plugins/*/*/src))
space := $(empty) $(empty)
PLUGIN_PATH := $(subst $(space),:,$(PLUGIN_SOURCES))
ANSIBLE_SOURCES = $(shell find plugins src/setup_system_sdk -type f -name '*.yml' | sort)
PLAYBOOKS = $(shell grep -lE '^[[:space:]]+hosts:' $(ANSIBLE_SOURCES))
SHELL_SOURCES = debian/publish $(wildcard *.sh) $(shell find debian docker libexec plugins -type f -name '*.sh' | sort)

.PHONY: lint typecheck test native-check native-build native-verify native-ci
lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked ruff format --check --line-length 88 docker/release.py docker/python_release.py
	$(UV) run --locked shellcheck $(SHELL_SOURCES)
	$(UV) run --locked yamllint -d '{rules: {key-duplicates: enable}}' .github plugins src/setup_system_sdk
	ANSIBLE_HOME="$(CURDIR)/coverage/ansible" $(UV) run --locked ansible-lint --offline $(ANSIBLE_SOURCES)
	ANSIBLE_HOME="$(CURDIR)/coverage/ansible" $(UV) run --locked ansible-playbook --syntax-check -i localhost, $(PLAYBOOKS)
typecheck:
	MYPYPATH="src:$(PLUGIN_PATH)" $(UV) run --locked mypy
	$(UV) run --locked mypy --python-version 3.12 --no-incremental docker/python_release.py
test:
	umask 022 && env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR PYTHONPATH="$(PLUGIN_PATH)" $(UV) run --locked pytest --cov --cov-report=json --cov-report=xml --cov-report=term-missing --junitxml=coverage/python/junit.xml
native-check: lint typecheck test
	$(UV) run --locked python docker/settings_reference.py
	$(UV) run --locked python docker/plugin_reference.py
	$(UV) lock --check
	$(UV) run --locked setup-system --help
native-build:
	$(UV) build --no-build-isolation --wheel --sdist --out-dir coverage/native-artifacts
native-verify: package
	$(UV) run --locked python docker/verify_native.py coverage/native-artifacts "$(PACKAGE_DIR)"
native-ci: native-check native-build native-verify
