.PHONY: help build up down restart logs test smoke clean

.DEFAULT_GOAL := help

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
	docker compose build

## Start the container (build first if needed)
up: build
	docker compose down --volumes 2>/dev/null || true
	docker compose up -d
	@echo "Waiting for server to start..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		curl -sf http://localhost:8000/api/v3 > /dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo "Server is up at http://localhost:8000"

## Stop and remove the container + volumes
down:
	docker compose down --volumes

## Rebuild and restart from scratch
restart: up

## Tail container logs
logs:
	docker compose logs -f

## Run the pytest suite (local, not in container)
test:
	uv run pytest tests/ -v

## Quick smoke test against the running container
smoke:
	@echo "=== Creating token ==="
	$(eval TOKEN := $(shell curl -sf -X POST 'http://localhost:8000/admin/tokens' \
		-H 'Content-Type: application/json' \
		-d '{"login":"admin","name":"smoke-token","scopes":["repo","user"]}' \
		| python3 -c "import sys,json; print(json.load(sys.stdin)['token'])"))
	@echo "Token: $(TOKEN)"
	@echo ""
	@echo "=== GET /user ==="
	@curl -sf -H "Authorization: token $(TOKEN)" http://localhost:8000/api/v3/user | python3 -m json.tool | head -5
	@echo ""
	@echo "=== Create repo ==="
	@curl -sf -X POST -H "Authorization: token $(TOKEN)" -H "Content-Type: application/json" \
		-d '{"name":"smoke-repo","description":"Smoke test"}' \
		http://localhost:8000/api/v3/user/repos | python3 -m json.tool | head -5
	@echo ""
	@echo "=== Create issue ==="
	@curl -sf -X POST -H "Authorization: token $(TOKEN)" -H "Content-Type: application/json" \
		-d '{"title":"Smoke test issue","body":"Testing"}' \
		http://localhost:8000/api/v3/repos/admin/smoke-repo/issues | python3 -m json.tool | head -5
	@echo ""
	@echo "=== List issues ==="
	@curl -sf http://localhost:8000/api/v3/repos/admin/smoke-repo/issues | python3 -m json.tool | head -5
	@echo ""
	@echo "=== Git clone + push ==="
	@rm -rf /tmp/smoke-clone
	@git clone http://localhost:8000/admin/smoke-repo.git /tmp/smoke-clone 2>&1 || true
	@cd /tmp/smoke-clone && git checkout -b main 2>/dev/null; \
		echo "# Smoke Test" > README.md; \
		git add README.md; \
		git -c commit.gpgsign=false -c user.name="Smoke" -c user.email="smoke@test.com" \
			commit -m "smoke test" 2>&1; \
		git -c commit.gpgsign=false push http://admin:$(TOKEN)@localhost:8000/admin/smoke-repo.git main 2>&1
	@echo ""
	@echo "=== Verify clone ==="
	@rm -rf /tmp/smoke-verify
	@git clone http://localhost:8000/admin/smoke-repo.git /tmp/smoke-verify 2>&1
	@cat /tmp/smoke-verify/README.md 2>/dev/null && echo "PASS: File content verified" || echo "FAIL: File not found"
	@rm -rf /tmp/smoke-clone /tmp/smoke-verify
	@echo ""
	@echo "=== Smoke test complete ==="

## Remove all build artifacts
clean: down
	docker rmi github_emulator_github-emulator 2>/dev/null || true
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
