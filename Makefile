.PHONY: help build up down restart logs dev test smoke frontend-install frontend-api frontend-check frontend-build actions-runner-env actions-real-runner actions-ui-smoke clean

.DEFAULT_GOAL := help

CONTAINER_ENGINE ?= docker
PORT ?= 8000
COMPOSE := $(CONTAINER_ENGINE) compose

# General

## Show this help
help:
	@echo "Usage: make <target>"
	@echo ""
	@awk 'BEGIN{section=""} \
		/^# -{10}/ { next } \
		/^# [A-Z]/ { if(section!="") print ""; printf "\033[1m%s\033[0m\n", substr($$0,3); section=$$0; next } \
		/^## /     { desc=substr($$0,4); next } \
		/^[a-zA-Z_-]+:/{ if(desc!="") { target=$$1; sub(/:.*/, "", target); printf "  \033[36m%-15s\033[0m %s\n", target, desc; desc="" } }' $(MAKEFILE_LIST)
	@echo ""

# Docker (local)

## Build the container image
build:
	$(COMPOSE) build

## Start the container (build first if needed)
up: build
	PORT=$(PORT) $(COMPOSE) down --volumes 2>/dev/null || true
	PORT=$(PORT) $(COMPOSE) up -d
	@echo "Waiting for server to start..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		curl -sf http://localhost:$(PORT)/api/v3 > /dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo "Server is up at http://localhost:$(PORT)"

## Stop and remove the container + volumes
down:
	$(COMPOSE) down --volumes

## Rebuild and restart from scratch
restart: up

## Tail container logs
logs:
	$(COMPOSE) logs -f

## Run the pytest suite (local, not in container)
test:
	uv run pytest tests/ -v

## Start the reloadable API and Vite frontend with Honcho
dev:
	uv run --extra dev honcho -f Procfile.dev start

## Install the locked API-client frontend dependencies
frontend-install:
	cd src/frontend && npm ci

## Regenerate the typed frontend API contract from FastAPI OpenAPI
frontend-api:
	PYTHONPATH=src uv run python scripts/export-openapi.py src/frontend/openapi.json
	cd src/frontend && npm run generate:api

## Run frontend type, lint, formatting, and component checks
frontend-check: frontend-api
	cd src/frontend && npm run typecheck
	cd src/frontend && npm run lint
	cd src/frontend && npm run format
	cd src/frontend && npm test

## Build the API-client frontend
frontend-build: frontend-api
	cd src/frontend && npm run build

SMOKE_API = http://localhost:$(PORT)/api/v3
SMOKE_DIR := /tmp/smoke-api
SMOKE_RESP := $(SMOKE_DIR)/response.json
# (smoke) smoke_status prints the response and exits 1 unless the HTTP status
# that the step's curl wrote to $code matches the case pattern $(1);
# smoke_expect then prints the start of a passing response. Each step checks
# the status itself, because the status of a `| head` pipeline is head's.
smoke_status = case "$$code" in $(1)) ;; *) \
		echo "FAIL: HTTP $$code, expected $(1)"; cat $(SMOKE_RESP) 2>/dev/null; echo; exit 1;; \
	esac
smoke_expect = $(call smoke_status,$(1)); python3 -m json.tool $(SMOKE_RESP) | head -5

## Quick smoke test against the running container
smoke:
	@rm -rf $(SMOKE_DIR) && mkdir -p $(SMOKE_DIR)
	@echo "=== Creating token ==="
	@rm -f $(SMOKE_RESP); \
		code=$$(curl -sS -o $(SMOKE_RESP) -w '%{http_code}' -X POST $(SMOKE_API)/admin/tokens \
			-H 'Content-Type: application/json' \
			-d '{"login":"admin","name":"smoke-token","scopes":["repo","user"]}'); \
		$(call smoke_status,201); \
		python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" \
			< $(SMOKE_RESP) > $(SMOKE_DIR)/token
	@echo "Token: $$(cat $(SMOKE_DIR)/token)"
	@echo ""
	@echo "=== GET /user ==="
	@TOKEN=$$(cat $(SMOKE_DIR)/token); rm -f $(SMOKE_RESP); \
		code=$$(curl -sS -o $(SMOKE_RESP) -w '%{http_code}' -H "Authorization: token $$TOKEN" \
			$(SMOKE_API)/user); \
		$(call smoke_expect,200)
	@echo ""
	@echo "=== Create repo ==="
	@TOKEN=$$(cat $(SMOKE_DIR)/token); rm -f $(SMOKE_RESP); \
		code=$$(curl -sS -o $(SMOKE_RESP) -w '%{http_code}' -X POST -H "Authorization: token $$TOKEN" \
			-H "Content-Type: application/json" -d '{"name":"smoke-repo","description":"Smoke test"}' \
			$(SMOKE_API)/user/repos); \
		if [ "$$code" = 422 ]; then \
			echo "smoke-repo already exists (HTTP 422), checking it"; rm -f $(SMOKE_RESP); \
			code=$$(curl -sS -o $(SMOKE_RESP) -w '%{http_code}' -H "Authorization: token $$TOKEN" \
				$(SMOKE_API)/repos/admin/smoke-repo); \
			$(call smoke_expect,200); \
		else \
			$(call smoke_expect,201); \
		fi
	@echo ""
	@echo "=== Create issue ==="
	@TOKEN=$$(cat $(SMOKE_DIR)/token); rm -f $(SMOKE_RESP); \
		code=$$(curl -sS -o $(SMOKE_RESP) -w '%{http_code}' -X POST -H "Authorization: token $$TOKEN" \
			-H "Content-Type: application/json" -d '{"title":"Smoke test issue","body":"Testing"}' \
			$(SMOKE_API)/repos/admin/smoke-repo/issues); \
		$(call smoke_expect,201)
	@echo ""
	@echo "=== List issues ==="
	@rm -f $(SMOKE_RESP); \
		code=$$(curl -sS -o $(SMOKE_RESP) -w '%{http_code}' $(SMOKE_API)/repos/admin/smoke-repo/issues); \
		$(call smoke_expect,200)
	@echo ""
	@echo "=== Git clone + push ==="
	@rm -rf /tmp/smoke-clone
	@git clone http://localhost:$(PORT)/admin/smoke-repo.git /tmp/smoke-clone 2>&1
	@TOKEN=$$(cat $(SMOKE_DIR)/token) && cd /tmp/smoke-clone && git checkout -B main && \
		echo "# Smoke Test" > README.md && \
		git add README.md && \
		git -c commit.gpgsign=false -c user.name="Smoke" -c user.email="smoke@test.com" \
			commit --allow-empty -m "smoke test" 2>&1 && \
		git -c commit.gpgsign=false push http://admin:$$TOKEN@localhost:$(PORT)/admin/smoke-repo.git main 2>&1
	@echo ""
	@echo "=== Verify clone ==="
	@rm -rf /tmp/smoke-verify
	@git clone http://localhost:$(PORT)/admin/smoke-repo.git /tmp/smoke-verify 2>&1
	@cat /tmp/smoke-verify/README.md 2>/dev/null && echo "PASS: File content verified" \
		|| { echo "FAIL: File not found"; rm -rf $(SMOKE_DIR) /tmp/smoke-clone /tmp/smoke-verify; exit 1; }
	@rm -rf $(SMOKE_DIR) /tmp/smoke-clone /tmp/smoke-verify
	@echo ""
	@echo "=== Smoke test complete ==="

## Create .env values for the compose actions-runner service
actions-runner-env:
	./scripts/actions-compose-bootstrap.sh

## Start the opt-in real actions/runner compose profile
actions-real-runner:
	$(COMPOSE) --profile real-runner up --build actions-real-runner

## Run desktop Playwright smoke test against a running compose stack
actions-ui-smoke:
	python3 scripts/actions-ui-smoke-playwright.py \
		--base-url "$${GITHUB_EMULATOR_UI_URL:-http://localhost:8000}" \
		--repo "$${RUNNER_REPO:-admin/test-repo}"

## Remove all build artifacts
clean: down
	$(CONTAINER_ENGINE) rmi github_emulator_github-emulator 2>/dev/null || true
	rm -rf .venv __pycache__ .pytest_cache

# Vagrant VM (Debian 12 + Docker, via libvirt/KVM)

.PHONY: vm-net vm-up vm-sync vm-build vm-start vm-stop vm-logs vm-deploy vm-destroy vm-ssh vm-ip vm-test vm-git-test vm-gh vm-client-sync vm-client-ssh

VM_IP := 192.168.123.10
VM_PROJECT_DIR := /srv/github_emulator

# (internal) ensure the libvirt network exists before booting
vm-net:
	@sudo virsh -c qemu:///system net-info ghemu_net >/dev/null 2>&1 \
		|| { echo '<network><name>ghemu_net</name><bridge name="virbr-ghemu" stp="on" delay="0"/><ip address="192.168.123.1" netmask="255.255.255.0"/></network>' \
		     | sudo virsh -c qemu:///system net-define /dev/stdin \
		     && sudo virsh -c qemu:///system net-start ghemu_net \
		     && sudo virsh -c qemu:///system net-autostart ghemu_net; }
	@sudo virsh -c qemu:///system net-start ghemu_net 2>/dev/null || true

## Boot the VMs (provisions on first run)
vm-up: vm-net
	vagrant up

## Rsync the codebase into the server VM
vm-sync:
	@echo "Syncing codebase to $(VM_PROJECT_DIR) ..."
	@vagrant ssh-config server > .vagrant-ssh-config
	rsync -avz --delete \
		--exclude '.venv' \
		--exclude '__pycache__' \
		--exclude '.git' \
		--exclude 'data/' \
		--exclude '.vagrant' \
		--exclude '.vagrant-ssh-config' \
		-e "ssh -F .vagrant-ssh-config" \
		. server:$(VM_PROJECT_DIR)/
	@rm -f .vagrant-ssh-config
	@echo "Sync complete."

## Build the container image inside the server VM
vm-build:
	vagrant ssh server -c "cd $(VM_PROJECT_DIR) && docker compose build"

## Start containers inside the server VM (fresh DB)
vm-start:
	vagrant ssh server -c "cd $(VM_PROJECT_DIR) && docker compose down --volumes 2>/dev/null; docker compose up -d"

## Stop containers inside the server VM
vm-stop:
	vagrant ssh server -c "cd $(VM_PROJECT_DIR) && docker compose down"

## Tail container logs inside the server VM
vm-logs:
	vagrant ssh server -c "cd $(VM_PROJECT_DIR) && docker compose logs -f"

## Sync, build, and start containers in server VM
vm-deploy: vm-sync vm-build vm-start
	@echo "Deploy complete. Service should be reachable at https://$(VM_IP)"

## Destroy all VMs
vm-destroy:
	vagrant destroy -f

## SSH into the server VM
vm-ssh:
	vagrant ssh server

## Print the VM IP for /etc/hosts
vm-ip:
	@echo "$(VM_IP)  ghemu.local"

# Testing

## Run gh CLI integration tests from the client VM
vm-test: vm-client-sync
	vagrant ssh client -c "bash /srv/scripts/gh-integration-test.sh"

## Run git CLI integration tests from the client VM
vm-git-test: vm-client-sync
	vagrant ssh client -c "bash /srv/scripts/git-integration-test.sh"

## Quick gh repo list from the client VM
vm-gh: vm-client-sync
	@echo "Creating token and running gh repo list ..."
	@vagrant ssh client -c '\
		TOKEN=$$(curl -sk https://ghemu.local/api/v3/admin/tokens \
			-X POST -H "Content-Type: application/json" \
			-d "{\"login\":\"admin\",\"name\":\"gh-test-$$$$\",\"scopes\":[\"repo\",\"user\"]}" \
			| jq -r .token) && \
		echo "Token: $$TOKEN" && \
		mkdir -p ~/.config/gh && \
		printf "ghemu.local:\n  oauth_token: %s\n  user: admin\n" "$$TOKEN" > ~/.config/gh/hosts.yml && \
		GH_INSECURE=1 GH_HOST=ghemu.local /srv/bin/gh repo list'

## Rsync gh binary + test scripts to client VM
vm-client-sync:
	@vagrant ssh-config client > .vagrant-ssh-config
	@rsync -avz \
		-e "ssh -F .vagrant-ssh-config" \
		$(CURDIR)/../bin/gh client:/srv/bin/gh
	@rsync -avz \
		-e "ssh -F .vagrant-ssh-config" \
		$(CURDIR)/scripts/ client:/srv/scripts/
	@rm -f .vagrant-ssh-config

## SSH into the client VM
vm-client-ssh:
	vagrant ssh client
