HOST_SOURCES := src sdk/src migrations conftest.py
TEST_SCOPE := $(if $(PLUGIN),plugins/$(PLUGIN),tests)
.PHONY: lint typecheck collect test infrastructure-check check
lint:
	$(UV) run --locked ruff check $(HOST_SOURCES) docker
	$(UV) run --locked ruff format --check $(HOST_SOURCES) docker
	$(UV) run --locked ruff format --check --line-length 88 docker/release.py docker/python_release.py
typecheck:
	$(UV) run --locked mypy
collect: prepare
	$(UV) run --no-sync python -m pytest -c pyproject.toml --collect-only $(TEST_SCOPE)
test: prepare
	$(UV) run --no-sync python -m pytest -c pyproject.toml $(TEST_SCOPE)
infrastructure-check: prepare
	$(UV) run --no-sync python docker/pytest_environment.py -c pyproject.toml $(TEST_SCOPE)
check: lint typecheck infrastructure-check
	$(UV) lock --check
