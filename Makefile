ifneq ($(CONTAINERIZED),1)
QUALITY_IMAGE := setupsystem:quality
RUNTIME_IMAGE := setupsystem:candidate
QUALITY_EXTRA_TARGETS := docs-check compile lock-check format-check
BUILD_DOCKERFILE := Dockerfile.quality
COVERAGE_REPORT := coverage/python/coverage.json
VERIFY_COMMAND = $(DOCKER) buildx build --file Dockerfile.quality $(DOCKER_BUILD_ARGS) --target installed-test --load --tag setupsystem:installed . && $(DOCKER) run --rm --init --read-only --network none --tmpfs /tmp:rw,mode=1777,size=64m setupsystem:installed
IMAGE_COMMAND = $(DOCKER) buildx build --file Dockerfile.quality $(DOCKER_BUILD_ARGS) --target runtime --load --tag $(RUNTIME_IMAGE) .
include docker/quality-python.mk
else
.DEFAULT_GOAL := help
UV ?= uv
.PHONY: help install lint typecheck test check build verify
help:
	@echo 'lint typecheck test check build verify'
install:
	$(UV) sync --locked
lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
typecheck:
	$(UV) run --locked mypy
test:
	$(UV) run --locked pytest --cov --cov-report=term-missing --cov-report=json --cov-report=xml
compile:
	$(UV) run --locked python -m compileall -q bootstrap.py src
lock-check:
	$(UV) lock --check
bootstrap-check:
	python bootstrap.py --help
	python bootstrap.py plan
check: lint typecheck compile lock-check bootstrap-check test
build:
	$(UV) build --no-build-isolation --wheel --sdist

endif
