HOST_SOURCES := src sdk/src migrations tests
.PHONY: lint typecheck test check
lint:
	$(UV) run --locked ruff check $(HOST_SOURCES) docker
	$(UV) run --locked ruff format --check $(HOST_SOURCES) docker
	$(UV) run --locked ruff format --check --line-length 88 docker/release.py docker/python_release.py
typecheck:
	$(UV) run --locked mypy
test:
	$(UV) run --locked pytest tests
check: lint typecheck test
	$(UV) lock --check
