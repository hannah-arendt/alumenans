# Developer tasks. Uses uv (https://docs.astral.sh/uv/) for the virtualenv
# and installs; everything runs inside .venv.
#
#   make install        create .venv and install the package + dev deps
#   make install-cpu    same, with the CPU-only torch build (much smaller on Linux)
#   make linux-gui      add the Qt toolkit pywebview needs to open a window on Linux
#   make test           run the full test suite
#   make test T=tests.test_proofreading   run one module / class / test
#   make run            launch the app

PYTHON_VERSION ?= 3.13
VENV           := .venv
PY             := $(VENV)/bin/python
T              ?=

TORCH_CPU_INDEX := https://download.pytorch.org/whl/cpu

.DEFAULT_GOAL := help
.PHONY: help venv install install-cpu linux-gui test pytest run self-test worker-help startup-check set-version wheel clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

$(PY):
	uv venv --python $(PYTHON_VERSION) $(VENV)

venv: $(PY) ## Create the virtualenv (.venv)

install: $(PY) ## Install the package (editable) with dev dependencies
	uv pip install --python $(PY) -e '.[dev]'

install-cpu: $(PY) ## Same as install, but with CPU-only torch
	uv pip install --python $(PY) -e '.[dev]' \
		--extra-index-url $(TORCH_CPU_INDEX) --index-strategy unsafe-best-match

linux-gui: $(PY) ## Add the Qt toolkit pywebview needs for `make run` on Linux
	uv pip install --python $(PY) -e '.[linux-gui]'

test: $(PY) ## Run tests with unittest (T=tests.test_x[.Class[.test]] for a subset)
ifeq ($(strip $(T)),)
	PYTHONPATH=src $(PY) -m unittest discover -s tests
else
	PYTHONPATH=src $(PY) -m unittest $(T)
endif

pytest: $(PY) ## Run tests with pytest
	$(PY) -m pytest

run: $(PY) ## Launch the app
	$(PY) -m alumenans

self-test: $(PY) ## Check the install works, without a window or models (what CI runs)
	PYTHONPATH=src $(PY) -m alumenans --self-test

worker-help: $(PY) ## Show the transcription worker's command-line options
	$(PY) -m alumenans.transcription.worker --help

startup-check: $(PY) ## Diagnose a slow or failing launch
	$(PY) tools/startup_check.py

set-version: ## Set the app version everywhere (V=x.y.z); releases pick one themselves
	@test -n "$(V)" || { echo "Usage: make set-version V=x.y.z"; exit 1; }
	python3 tools/version.py set $(V)

wheel: ## Build a wheel into dist/
	uv build --wheel -o dist .

clean: ## Remove build artifacts and caches (keeps .venv)
	rm -rf build dist src/*.egg-info .pytest_cache
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -prune -exec rm -rf {} +
