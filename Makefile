# fleetkit harness: the idempotent targets that set up and run one host (Mac or EC2).
# Design: docs/harness-design.md. Every target is safe to re-run; `make help` lists them.
#
#   make venv         python venv for telemetry, host daemon, driver and guest tests (harness/.venv)
#   make test         offline pytest suites under harness/*
#   make guest-image  build fleetkit-guest:dev from images/guest/Dockerfile
#   make rootfs       pack guest.ext4 (Linux host only; prints a skip on macOS)
#   make fixture      build the static shopping site into fixture/dist
#   make up           observability stack + fixture (LGTM=0: fixture only)
#   make down         stop them (results/lgtm is kept)
#   make lgtm-check   send one span, expect it in results/lgtm/otlp/traces.jsonl, ping Grafana
#   make hostd        run the host daemon in the foreground on :8090
#   make smoke        driver smoke suite against BACKEND (docker by default)
#   make clean        build outputs, caches, stray session containers
#   make distclean    clean + venv + results/lgtm

SHELL := /bin/bash
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UNAME_S := $(shell uname -s)

# Pinned images; images/lock.env (owned by the image build) overrides these when present.
DEBIAN_IMAGE ?= debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
NGINX_IMAGE ?= nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de
LGTM_IMAGE ?= grafana/otel-lgtm:0.34.0@sha256:b966ea107831d526d9eb8fe4d2d86c9e5731392fad9dce8296bcf2072031f07c
ALPINE_IMAGE ?= alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8
-include images/lock.env
export DEBIAN_IMAGE NGINX_IMAGE LGTM_IMAGE ALPINE_IMAGE

VENV := harness/.venv
PY := $(VENV)/bin/python
PYTHON3 ?= python3
# hostd, driver and guestd packages live one level below their component directories;
# fleetkit_telemetry is installed editable, the path entry makes it importable without that too.
export PYTHONPATH := $(ROOT)/harness/host:$(ROOT)/harness/driver:$(ROOT)/harness/telemetry:$(ROOT)/harness/guest

GUEST_IMAGE ?= fleetkit-guest:dev
BACKEND ?= docker
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
RESULTS ?= results
LGTM ?= 1
export FIXTURE_BIND ?= 127.0.0.1
HOSTD_ARGS ?=
DRIVER_ARGS ?=
# LGTM=0 turns off OTLP export in both components (they spell the switch differently).
ifeq ($(LGTM),0)
NO_LGTM := --no-lgtm
HOSTD_NO_LGTM := --no-otlp
endif
HOSTD_LOG_DIR ?= $(RESULTS)/hostd

# docker compose where it exists; the plain-docker script otherwise (AL2023 has no compose package).
HAVE_COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo 1)
COMPOSE := docker compose -f observability/compose.yaml

.PHONY: help venv test guest-image rootfs fixture up down lgtm-check hostd smoke clean clean-lgtm distclean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(firstword $(MAKEFILE_LIST)) | sed 's/:.*## /\t/' | sort | column -t -s $$'\t'

# --- python ------------------------------------------------------------------------------

$(VENV)/bin/activate:
	$(PYTHON3) -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip

venv: $(VENV)/bin/activate ## create harness/.venv and install the telemetry, host, driver and guest requirements
	@echo '*' > $(VENV)/.gitignore
	$(VENV)/bin/pip install -q -r harness/telemetry/requirements.txt -e harness/telemetry
	@for req in harness/host/requirements.txt harness/driver/requirements.txt harness/guest/requirements.txt; do \
	  if [ -f $$req ]; then echo "pip install -r $$req"; $(VENV)/bin/pip install -q -r $$req; fi; done

test: venv ## offline pytest suites under harness/telemetry, harness/host, harness/driver, harness/guest
	@for comp in telemetry host driver guest; do \
	  if [ -d harness/$$comp/tests ]; then echo "== harness/$$comp"; (cd harness/$$comp && $(ROOT)/$(PY) -m pytest -q tests) || exit 1; fi; done

# --- images ------------------------------------------------------------------------------

guest-image: ## build the guest image for the local architecture
	docker build -f images/guest/Dockerfile --build-arg DEBIAN_IMAGE=$(DEBIAN_IMAGE) -t $(GUEST_IMAGE) .

rootfs: ## export the guest image into guest.ext4 (needs a Linux docker host)
ifeq ($(UNAME_S),Darwin)
	@echo "rootfs: skipped on macOS; the Firecracker root filesystem is built on the Linux host (make rootfs there)."
else
	GUEST_IMAGE=$(GUEST_IMAGE) ALPINE_IMAGE=$(ALPINE_IMAGE) images/guest/build-rootfs.sh
endif

# --- fixture and observability ------------------------------------------------------------

fixture: ## build the static shopping site into fixture/dist
	$(PYTHON3) fixture/build.py

$(RESULTS)/.gitignore:
	@mkdir -p $(RESULTS)
	@echo '*' > $@

up: $(RESULTS)/.gitignore ## start observability (LGTM=0 skips it) and the fixture on 127.0.0.1:8081
	@test -d fixture/dist || $(MAKE) fixture
	@mkdir -p $(RESULTS)/lgtm/data $(RESULTS)/lgtm/otlp
ifeq ($(HAVE_COMPOSE),1)
ifeq ($(LGTM),0)
	$(COMPOSE) up -d --wait fixture
else
	$(COMPOSE) up -d --wait
endif
else
	observability/run-plain.sh up $(NO_LGTM)
endif
	@echo "fixture: http://$(FIXTURE_BIND):8081  grafana: http://127.0.0.1:3000  otlp: http://127.0.0.1:4318"

down: ## stop the observability stack and the fixture (results/lgtm is kept)
ifeq ($(HAVE_COMPOSE),1)
	$(COMPOSE) down --remove-orphans
else
	observability/run-plain.sh down
endif

lgtm-check: ## prove the pipeline: one span in, a line in results/lgtm/otlp/traces.jsonl, Grafana up
	$(PYTHON3) observability/check.py --otlp-dir $(RESULTS)/lgtm/otlp

# --- run ---------------------------------------------------------------------------------

hostd: venv ## run the host daemon in the foreground (BACKEND=docker|firecracker, HOSTD_ARGS=...)
	$(PY) -m hostd --backend $(BACKEND) --log-dir $(HOSTD_LOG_DIR) $(HOSTD_NO_LGTM) $(HOSTD_ARGS)

smoke: venv ## run the driver smoke suite against a running host daemon
	$(PY) -m driver smoke --backend $(BACKEND) --out $(RESULTS)/smoke-$(RUN_ID) $(NO_LGTM) $(DRIVER_ARGS)

# --- cleanup -----------------------------------------------------------------------------

clean: ## remove build outputs, caches and any leftover session containers
	rm -rf fixture/dist
	find . -path ./$(VENV) -prune -o \( -name __pycache__ -o -name .pytest_cache -o -name '*.egg-info' \) -type d -print0 | xargs -0 rm -rf
	@ids=$$(docker ps -aq -f label=fleetkit.role=session 2>/dev/null); if [ -n "$$ids" ]; then docker rm -f $$ids; fi

clean-lgtm: down ## remove the observability stack's state and OTLP files under results/lgtm
	rm -rf $(RESULTS)/lgtm

distclean: clean clean-lgtm ## clean plus the venv
	rm -rf $(VENV)
