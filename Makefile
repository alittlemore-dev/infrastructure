TRIVY_IMAGE := docker.io/aquasec/trivy:0.70.0@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e

.PHONY: run
run:
	bash infra/scripts/run.sh

.PHONY: stop
stop:
	bash infra/scripts/stop.sh

.PHONY: certbot-issue
certbot-issue:
	bash infra/scripts/tls.sh issue

.PHONY: certbot-renew
certbot-renew:
	bash infra/scripts/tls.sh renew

.PHONY: certbot-sync
certbot-sync:
	bash infra/scripts/tls.sh sync

.PHONY: agent-ca-init
agent-ca-init:
	bash infra/scripts/agent_ca.sh init "$(OFFLINE_ROOT_DIR)" "$(ISSUING_DIR)"

.PHONY: agent-client-csr
agent-client-csr:
	bash infra/scripts/agent_ca.sh client-csr "$(AGENT_ID)" "$(CLIENT_OUTPUT_DIR)"

.PHONY: tests
tests:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

.PHONY: check
check:
	bash infra/scripts/check.sh

.PHONY: lint-dockerfiles
lint-dockerfiles:
	bash infra/scripts/docker_lint.sh

.PHONY: security-trivy-config
security-trivy-config:
	bash infra/scripts/trivy_scan.sh config "$(TRIVY_IMAGE)"

.PHONY: security-trivy-images
security-trivy-images:
	bash infra/scripts/trivy_scan.sh images "$(TRIVY_IMAGE)"

.PHONY: quality
quality: tests check lint-dockerfiles security-trivy-config
