UV ?= uv
ifeq ($(CONTAINERIZED),1)
RUFF ?= ruff
else
RUFF ?= uvx --from ruff==0.17.1 ruff
endif
# Fast native checks never prepare a product/build image. The Docker CI child
# keeps its wrapper so test/coverage receipts describe the container execution.
ifneq ($(SKIP_QUALITY_BUILD),1)
ifneq ($(filter lint format format-check check,$(MAKECMDGOALS)),)
override CONTAINERIZED := 1
endif
endif
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
CI_LOCAL_TARGETS ?= check coverage build verify release-check
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
	$(RUFF) check .
	$(RUFF) format --check .
	$(RUFF) format --check --line-length 88 docker/release.py docker/python_release.py
typecheck:
	$(UV) run --locked mypy
	$(UV) run --locked mypy --python-version 3.12 --no-incremental docker/python_release.py
test:
	$(UV) run --locked pytest --cov --cov-report=term-missing --cov-report=json --cov-report=xml --junitxml=coverage/python/junit.xml
compile:
	$(UV) run --locked python -m compileall -q bootstrap.py src
lock-check:
	$(UV) lock --check
bootstrap-check:
	$(UV) run --locked python bootstrap.py --help
	$(UV) run --locked python docker/check_bootstrap.py
check: lint typecheck compile lock-check bootstrap-check test ## Native fast gate; no system apply.

acceptance: check
build:
	$(UV) build --no-build-isolation --wheel --sdist --out-dir coverage/artifacts

endif

.PHONY: native-check native-build native-verify native-ci test-integration
NATIVE_ARTIFACT_DIR ?= coverage/native-artifacts
native-check:
	$(MAKE) CONTAINERIZED=1 RUFF="$(UV) run --locked ruff" check
native-build:
	$(UV) build --no-build-isolation --wheel --sdist --out-dir "$(NATIVE_ARTIFACT_DIR)"
native-verify:
	$(UV) run --locked python docker/verify_native.py "$(NATIVE_ARTIFACT_DIR)"
native-ci: native-check native-build native-verify

test-integration:
	$(UV) run --locked pytest -q tests/test_python_release.py tests/test_templates.py

.PHONY: native-help
help: native-help
native-help:
	@printf '%s\n' 'Native: make native-check; make native-ci (locked Python/uv; no Docker).' \
	  'Flow: lowerCamelCase working branch -> test -> dev -> main; exact-SHA CI releases.'
