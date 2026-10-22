PYTHON ?= python3
VENV ?= .venv
PORT ?= 8765
ARGS ?=
IMAGE ?= potosi/overwatch:local
DOCKER ?= docker
CODEX_HOME ?= $(HOME)/.codex
CLAUDE_HOME ?= $(HOME)/.claude

VENV_PYTHON = $(VENV)/bin/python
VENV_COMMAND = $(VENV)/bin/overwatch

.DEFAULT_GOAL := help

.PHONY: help venv install setup install-system dev demo test check docker-build docker-run docker-demo

help:
	@printf '%s\n' 'venv          Create the local Python virtual environment' 'install       Install Overwatch into the virtual environment (editable)' 'install-system Install the system service (requires administrator auth)' 'dev           Run the local server with ARGS (for example ARGS=--demo)' 'demo          Run the local server with sample data' 'test          Run the Python behavioral tests' 'check         Run the project checks' 'docker-build  Build the local Docker image' 'docker-run    Run against local Codex and Claude logs' 'docker-demo   Run the container with sample data'

venv: $(VENV_PYTHON)

$(VENV_PYTHON):
	$(PYTHON) -m venv "$(VENV)"

install: venv
	"$(VENV_PYTHON)" -m pip install -e .

setup: install

install-system:
	sudo python3 scripts/install-system.py --user "$$(id -un)"

dev: install
	"$(VENV_COMMAND)" --port $(PORT) $(ARGS)

demo: install
	"$(VENV_COMMAND)" --port $(PORT) --demo $(ARGS)

test: install
	"$(VENV_PYTHON)" -m unittest discover -s tests -v

check: test

docker-build:
	$(DOCKER) build -t $(IMAGE) .

docker-run:
	@set --; \
	  if [ -d "$(CODEX_HOME)" ]; then set -- "$$@" --mount "type=bind,source=$(CODEX_HOME),target=/sessions/codex,readonly"; fi; \
	  if [ -d "$(CLAUDE_HOME)" ]; then set -- "$$@" --mount "type=bind,source=$(CLAUDE_HOME),target=/sessions/claude,readonly"; fi; \
	  $(DOCKER) run --rm --user "$$(id -u):$$(id -g)" -p 127.0.0.1:$(PORT):$(PORT) "$$@" $(IMAGE) --port $(PORT) --codex-home /sessions/codex --claude-home /sessions/claude $(ARGS)

docker-demo:
	$(DOCKER) run --rm -p 127.0.0.1:$(PORT):$(PORT) $(IMAGE) --port $(PORT) --demo $(ARGS)
