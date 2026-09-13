.DEFAULT_GOAL := help

QUALITY_TOOLS_DIR ?= $(CURDIR)/.cache/quality-tools

.PHONY: help
help:
	@printf '%-30s %s\n' 'Available targets:' ''
	@printf '%-30s %s\n' '  quality' 'Run the complete local/CI quality gate.'
	@printf '%-30s %s\n' '  tools' 'Resolve or install pinned quality tools.'
	@printf '%-30s %s\n' '  tests' 'Run all tests, including the SOPS/age round trip.'
	@printf '%-30s %s\n' '  validate' 'Validate scripts, JSON, manifests, and Compose config.'
	@printf '%-30s %s\n' '  lint' 'Lint every Dockerfile and shell script.'
	@printf '%-30s %s\n' '  security-config' 'Scan repository configuration with Trivy.'
	@printf '%-30s %s\n' '  security-images' 'Build, pull, and scan all runtime images.'
	@printf '%-30s %s\n' '  doctor' 'Check local quality prerequisites.'
	@printf '%-30s %s\n' '  doctor-runtime' 'Check production-host prerequisites.'
	@printf '%-30s %s\n' '  status' 'Show the active deployment and project containers.'
	@printf '%-30s %s\n' '  dependencies-status' 'Check manually pinned dependencies for upstream updates.'
	@printf '%-30s %s\n' '  dev' 'Build and start applications from local sibling checkouts.'
	@printf '%-30s %s\n' '  dev-trust' 'Trust the generated local HTTPS certificate authority.'
	@printf '%-30s %s\n' '  secrets-verify' 'Verify every tracked SOPS document without plaintext output.'
	@printf '%-30s %s\n' '  deploy' 'Run the production-oriented blue/green rollout.'
	@printf '%-30s %s\n' '  stop' 'Stop the stack without deleting named volumes.'
	@printf '%-30s %s\n' '  certbot-{issue,renew,sync}' 'Manage the shared TLS certificate.'
	@printf '%-30s %s\n' '  agent-ca-init' 'Create offline root and issuing Agent CAs.'
	@printf '%-30s %s\n' '  agent-client-csr' 'Create an Agent client key and CSR.'

.PHONY: deploy run
deploy:
	bash infra/scripts/run.sh
run: deploy

.PHONY: dev dev-trust
dev:
	bash infra/scripts/dev.sh
dev-trust:
	bash infra/scripts/dev_tls.sh trust

.PHONY: stop
stop:
	bash infra/scripts/stop.sh

.PHONY: certbot-issue certbot-renew certbot-sync
certbot-issue:
	bash infra/scripts/tls.sh issue
certbot-renew:
	bash infra/scripts/tls.sh renew
certbot-sync:
	bash infra/scripts/tls.sh sync

.PHONY: agent-ca-init agent-client-csr
agent-ca-init:
	bash infra/scripts/agent_ca.sh init "$(OFFLINE_ROOT_DIR)" "$(ISSUING_DIR)"
agent-client-csr:
	bash infra/scripts/agent_ca.sh client-csr "$(AGENT_ID)" "$(CLIENT_OUTPUT_DIR)"

.PHONY: tools
tools:
	python3 infra/scripts/quality_tools.py ensure --cache-dir "$(QUALITY_TOOLS_DIR)"

.PHONY: tests
tests:
	python3 infra/scripts/run_tests.py --cache-dir "$(QUALITY_TOOLS_DIR)"

.PHONY: validate check
validate:
	bash infra/scripts/check.sh
check: validate

.PHONY: lint lint-dockerfiles
lint:
	bash infra/scripts/docker_lint.sh
lint-dockerfiles: lint

.PHONY: security-config security-trivy-config
security-config:
	bash infra/scripts/trivy_scan.sh config
security-trivy-config: security-config

.PHONY: security-images security-trivy-images
security-images:
	bash infra/scripts/trivy_scan.sh images
security-trivy-images: security-images

.PHONY: doctor doctor-runtime
doctor:
	bash infra/scripts/doctor.sh quality
doctor-runtime:
	bash infra/scripts/doctor.sh runtime

.PHONY: status
status:
	bash infra/scripts/status.sh

.PHONY: dependencies-status
dependencies-status:
	python3 infra/scripts/dependencies_status.py --repo-dir .

.PHONY: secrets-verify
secrets-verify:
	python3 infra/scripts/verify_sops_documents.py \
		--manifest infra/deploy/runtime-secrets.manifest.json \
		--repo-dir . \
		--cache-dir "$(QUALITY_TOOLS_DIR)" \
		--age-key-file "$(SOPS_AGE_KEY_FILE)"

.PHONY: quality
quality: tests validate lint security-config
