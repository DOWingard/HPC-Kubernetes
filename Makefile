SHELL := /usr/bin/env bash
export PATH := $(CURDIR)/.tools/bin:$(PATH)
export KUBECONFIG := $(CURDIR)/.data/kubeconfig

.DEFAULT_GOAL := help
.PHONY: help tools env images up down clean status slurm reconfigure demo test argocd-password logs

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

tools: ## Install pinned k3d, kubectl and helm into .tools/bin
	@scripts/bootstrap-tools.sh

env: ## Generate .env with random secrets (no-op if it exists)
	@scripts/init-env.sh

images: ## Build the Slurm, bare-metal and portal images
	@scripts/build-images.sh

up: tools env images ## Create (or reconcile) the whole lab
	@scripts/up.sh

down: ## Delete the cluster, bare-metal node and Docker network
	@scripts/down.sh

clean: ## down + delete the shared filesystem and kubeconfig
	@scripts/down.sh --purge

status: ## Show Slurm, Kubernetes and bare-metal node state
	@scripts/status.sh

slurm: ## Run a Slurm command on the controller, e.g. make slurm CMD="squeue"
	@kubectl -n hpc exec slurmctld-0 -c slurmctld -- $(or $(CMD),sinfo)

reconfigure: ## Push slurm.conf/cgroup.conf changes to the running cluster
	@scripts/reconfigure.sh

demo: ## Submit jobs through the REST API and watch them run on both partitions
	@scripts/demo.sh

test: ## Run the portal's unit tests
	@cd app && ( [ -d .venv ] || python3 -m venv .venv ) && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/pytest -q

argocd-password: ## Print the Argo CD initial admin password
	@kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo

logs: ## Tail slurmctld (incl. burst scaling) logs
	@kubectl -n hpc logs -f slurmctld-0 -c slurmctld
