UV ?= uv
PLUGIN_SOURCES := $(sort $(wildcard plugins/*/src))
space := $(empty) $(empty)
PLUGIN_PATH := $(subst $(space),:,$(PLUGIN_SOURCES))
ANSIBLE_SOURCES = $(shell find plugins src/setup_system/execution/recipes -type f -name '*.yml' | sort)
PLAYBOOKS = $(shell grep -lE '^[[:space:]]+hosts:' $(ANSIBLE_SOURCES))
SHELL_SOURCES = debian/publish $(wildcard *.sh) $(shell find debian docker libexec plugins -type f -name '*.sh' | sort)

.PHONY: lint typecheck test native-check native-build native-verify native-ci
lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked ruff format --check --line-length 88 docker/release.py docker/python_release.py
	$(UV) run --locked shellcheck $(SHELL_SOURCES)
	$(UV) run --locked yamllint -d '{rules: {key-duplicates: enable}}' .github plugins src/setup_system/execution/recipes
	ANSIBLE_HOME="$(CURDIR)/coverage/ansible" $(UV) run --locked ansible-lint --offline $(ANSIBLE_SOURCES)
	ANSIBLE_HOME="$(CURDIR)/coverage/ansible" $(UV) run --locked ansible-playbook --syntax-check -i localhost, $(PLAYBOOKS)
typecheck:
	MYPYPATH="src:$(PLUGIN_PATH)" $(UV) run --locked mypy
	$(UV) run --locked mypy --python-version 3.12 --no-incremental docker/python_release.py
test:
	@if find tests plugins -type f \( -name 'test_*.py' -o -name '*_test.py' \) | grep -q .; then \
	    $(UV) run --locked pytest; \
	else printf '%s\n' 'No automated product cases are defined. Behavioural coverage is unavailable.'; fi

native-check: lint typecheck test
	$(UV) run --locked python -X pycache_prefix=coverage/compile-cache -m compileall -q src plugins migrations debian docker libexec tests
	$(UV) run --locked python -c 'import setup_system.app, setup_system.storage.models, setup_system_sdk.worker'
	@set -eu; d=$$(mktemp -d); trap 'rm -rf "$$d"' EXIT; \
	  HOME="$$d" XDG_STATE_HOME="$$d/state" $(UV) run --locked --no-sync python -m setup_system.storage.migrations upgrade; \
	  HOME="$$d" XDG_STATE_HOME="$$d/state" $(UV) run --locked --no-sync python -m setup_system.storage.migrations check
	$(UV) run --locked python docker/settings_reference.py
	$(UV) run --locked python docker/plugin_reference.py
	$(UV) lock --check
	$(UV) run --locked setup-system --help
native-build:
	$(UV) build --no-build-isolation --wheel --sdist --out-dir coverage/native-artifacts
native-verify: package
	$(UV) run --locked python docker/verify_native.py coverage/native-artifacts "$$PACKAGE_DIR"
native-ci: native-check native-build native-verify
