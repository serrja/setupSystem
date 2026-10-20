ifneq ($(CONTAINERIZED),1)
QUALITY_IMAGE := setupsystem:quality
RUNTIME_IMAGE := setupsystem:candidate
QUALITY_EXTRA_TARGETS := compile lock-check bootstrap-check
BUILD_DOCKERFILE := Dockerfile.quality
COVERAGE_REPORT := coverage/python/coverage.json
INSTALLED_TEST_IMAGE := setupsystem:installed
ifeq ($(OFFLINE),1)
QUALITY_RUN_ARGS += --pull=never --env UV_OFFLINE=true
BUILD_COMMAND = $(RELEASE_CHECK_COMMAND)
else
BUILD_COMMAND = $(DOCKER) buildx build --file Dockerfile.quality $(DOCKER_BUILD_ARGS) $(LOCAL_BUILD_NETWORK) --target artifacts --output type=local,dest=coverage/artifacts .
endif
BUILD_RECEIPT_COMMAND = $(QUALITY_RUN) python docker/release.py capture-build --artifact-dir coverage/artifacts
RELEASE_CHECK_COMMAND = $(QUALITY_RUN) python docker/release.py release-check --artifact-dir coverage/artifacts
OFFLINE_PREFLIGHT_COMMAND = $(MAKE) setup-offline-preflight
VERIFY_COMMAND = $(MAKE) verify-cli-installed
IMAGE_COMMAND = $(DOCKER) buildx build --file Dockerfile.quality $(DOCKER_BUILD_ARGS) $(LOCAL_BUILD_NETWORK) --target runtime --load --tag $(RUNTIME_IMAGE) .
include docker/quality-python.mk

.PHONY: setup-offline-preflight verify-cli-installed
setup-offline-preflight: source-identity
	@test -s "$(REPORT_DIR)/installed/test-image-id.txt" -a -s "$(REPORT_DIR)/installed/source.json" || { echo "BLOCKED: prepare installed CLI acceptance online first." >&2; exit 2; }
	@test "$$(cat $(REPORT_DIR)/installed/test-image-id.txt)" = "$$($(DOCKER) image inspect $(INSTALLED_TEST_IMAGE) --format '{{.Id}}')" || { echo "BLOCKED: prepared installed CLI image missing or replaced." >&2; exit 2; }
	@test "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/source.json)" = "$$(sed -n 's/.*"contentSha256":"\([a-f0-9]*\)".*/\1/p' $(REPORT_DIR)/installed/source.json)" || { echo "BLOCKED: installed CLI source changed." >&2; exit 2; }
	$(RELEASE_CHECK_COMMAND)
verify-cli-installed: source-identity
	@mkdir -p "$(REPORT_DIR)/installed"
ifneq ($(OFFLINE),1)
	$(DOCKER) buildx build --file Dockerfile.quality $(DOCKER_BUILD_ARGS) $(LOCAL_BUILD_NETWORK) --target installed-test --load --tag $(INSTALLED_TEST_IMAGE) .
else
	$(MAKE) setup-offline-preflight
endif
	$(DOCKER) run --pull=never --rm --init --read-only --network none --tmpfs /tmp:rw,mode=1777,size=64m $(INSTALLED_TEST_IMAGE)
	@cp "$(REPORT_DIR)/source.json" "$(REPORT_DIR)/installed/source.json"
	@$(DOCKER) image inspect $(INSTALLED_TEST_IMAGE) --format '{{.Id}}' > "$(REPORT_DIR)/installed/test-image-id.txt"
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
	$(UV) run --locked ruff format --check --line-length 88 docker/release.py
typecheck:
	$(UV) run --locked mypy
test:
	$(UV) run --locked pytest --cov --cov-report=term-missing --cov-report=json --cov-report=xml --junitxml=coverage/python/junit.xml
compile:
	$(UV) run --locked python -m compileall -q bootstrap.py src
lock-check:
	$(UV) lock --check
bootstrap-check:
	python bootstrap.py --help
	python bootstrap.py plan
check: lint typecheck compile lock-check bootstrap-check test
build:
	$(UV) build --no-build-isolation --wheel --sdist --out-dir coverage/artifacts

endif
