#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"


class CertificateContractTest(unittest.TestCase):
    def test_public_config_uses_the_atomic_certificate_release(self) -> None:
        platform_config = (ROOT / "config/platform/production.env").read_text(encoding="utf-8")

        self.assertIn("SSL_CERT=/certs/current/fullchain.pem", platform_config)
        self.assertIn("SSL_KEY=/certs/current/privkey.pem", platform_config)

    def test_authentication_public_and_private_keys_must_match(self) -> None:
        script = (ROOT / "infra/scripts/compose_secrets.sh").read_text(encoding="utf-8")
        self.assertIn('printf \'%b\' "$COMPETENCY_AUTH_PUBLIC_KEY"', script)
        self.assertIn('openssl pkey -pubin -in "$auth_public_key"', script)
        self.assertIn('cmp -s "$auth_declared_public_key" "$auth_private_public_key"', script)

    def test_certificate_sync_validates_before_atomic_activation(self) -> None:
        script = (ROOT / "infra/scripts/cert_sync.sh").read_text(encoding="utf-8")
        for hostname in (
            '"$app_domain"',
            '"$minio_domain"',
            '"agent.${app_domain}"',
        ):
            self.assertIn(hostname, script)
        self.assertLess(script.index("openssl x509 -in"), script.index('mv -Tf "$temporary_link"'))
        self.assertIn('chmod 644 "${staging_directory}/fullchain.pem"', script)
        self.assertIn('chmod 640 "${staging_directory}/privkey.pem"', script)

    def test_root_certificate_sync_is_strictly_sandboxed_and_documented(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        cert_sync = compose.split("  cert-sync:\n", maxsplit=1)[1].split(
            "\nnetworks:\n", maxsplit=1
        )[0]
        ignore = (ROOT / ".trivyignore.yaml").read_text(encoding="utf-8")

        for restriction in (
            'user: "0:0"',
            "read_only: true",
            "cap_drop:\n      - ALL",
            "cap_add:\n      - CHOWN\n      - DAC_OVERRIDE",
            "security_opt:\n      - no-new-privileges:true",
            "network_mode: none",
            "- letsencrypt:/etc/letsencrypt:ro",
        ):
            self.assertIn(restriction, cert_sync)
        self.assertIn("id: DS-0002", ignore)
        self.assertIn("- infra/cert-sync/Dockerfile", ignore)

    def test_rollout_preflights_nginx_and_checks_the_local_edge(self) -> None:
        run_script = (ROOT / "infra/scripts/run.sh").read_text(encoding="utf-8")
        edge_checks = (ROOT / "infra/scripts/edge_checks.sh").read_text(encoding="utf-8")
        main_sequence = run_script.split("\npull_application_images\n", maxsplit=1)[1]
        self.assertLess(
            main_sequence.index("build_and_validate_candidate_edge"),
            main_sequence.index("trap handle_edge_interruption"),
        )
        self.assertIn("/usr/local/bin/alittlemore-nginx-entrypoint", run_script)
        self.assertIn("--pull never", run_script)
        self.assertIn('--resolve "${hostname}:443:127.0.0.1"', edge_checks)
        self.assertIn('"${APP_DOMAIN}|/ru/how-this-site-is-built"', edge_checks)
        self.assertIn("verify_served_edge_certificates", run_script)

    def test_tls_reload_runs_nginx_syntax_and_served_certificate_checks(self) -> None:
        tls_script = (ROOT / "infra/scripts/tls.sh").read_text(encoding="utf-8")
        self.assertLess(tls_script.index("nginx -t"), tls_script.index("nginx -s reload"))
        self.assertIn("verify_served_edge_certificates", tls_script)
        self.assertIn("docker compose build cert-sync", tls_script)
        self.assertIn("docker compose run --rm --pull never cert-sync", tls_script)


if __name__ == "__main__":
    unittest.main()
