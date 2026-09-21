from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class I18nContractTest(unittest.TestCase):
    def test_edge_uses_only_bundle_scoped_i18n_contract(self) -> None:
        nginx = (ROOT / 'infra/nginx/templates/site.conf.template').read_text()
        local_smoke = (ROOT / 'infra/scripts/dev.sh').read_text()
        production_smoke = (ROOT / 'infra/scripts/edge_checks.sh').read_text()

        for source in (local_smoke, production_smoke):
            self.assertIn('/api/i18n/bundles/shared/ru', source)
            self.assertIn('/api/i18n/bundles/how-this-site-is-built/en', source)
            self.assertIn('/api/i18n/bundles/personal-workspace/en', source)
            self.assertIn('retired_i18n_paths', source)
            self.assertIn('/api/i18n/bundles/ru', source)
            self.assertIn('/api/i18n/personal-workspace/bundles/ru', source)
            self.assertIn('/api/personal-workspace/i18n/bundles/ru', source)
            self.assertIn('expected 404', source)
        self.assertNotIn('location = /api/personal-workspace/i18n/languages', nginx)
        self.assertNotIn('location ^~ /api/personal-workspace/i18n/bundles/', nginx)

    def test_production_service_has_scoped_cache_secrets_and_ready_probe(self) -> None:
        manifest = json.loads((ROOT / 'infra/deploy/runtime-secrets.manifest.json').read_text())
        env = dict(os.environ, IMAGE_REGISTRY='registry.example.test/app',
                   APP_DOMAIN='app.example.test', APP_URL_SCHEMA='https')
        for document in manifest['documents']:
            for secret in document['secrets']:
                env[secret['composeVariable']] = '/dev/null'
        result = subprocess.run(['docker', 'compose', '--env-file', 'config/platform/development.env',
                                 '-f', 'docker-compose.yml', 'config', '--format', 'json'],
                                cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        services = json.loads(result.stdout)['services']
        for slot in ('blue', 'green'):
            service = services[f'i18n-backend-{slot}']
            self.assertEqual({'i18n-network'}, set(service['networks']))
            self.assertEqual('i18n-valkey', service['environment']['VALKEY_HOST'])
            self.assertEqual('ru', service['environment']['I18N_DEFAULT_LANGUAGE'])
            self.assertEqual({'i18n-valkey'}, set(service['depends_on']))
            self.assertIn('/api/i18n/healthcheck/ready', service['healthcheck']['test'][-1])
            self.assertEqual({'sentry_dsn'}, {secret['target'] for secret in service['secrets']})
            self.assertNotIn('ports', service)
        for slot in ('blue', 'green'):
            frontend = services[f'frontend-{slot}']
            self.assertIn('i18n-network', frontend['networks'])
            self.assertEqual(f'http://i18n-backend-{slot}:8080',
                             frontend['environment']['SSR_I18N_ORIGIN'])
        self.assertIn('i18n-network', services['nginx']['networks'])
        self.assertEqual({'i18n-network'}, set(services['i18n-valkey']['networks']))
        self.assertNotIn('ports', services['i18n-valkey'])

    def test_previous_slot_cleanup_and_rollback_restore_i18n(self) -> None:
        source = (ROOT / 'infra/scripts/run.sh').read_text()
        functions = source[source.index('other_slot() {'):source.index('\nrequire_docker_compose\n')]
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'test.sh'
            script.write_text('''set -euo pipefail
COMPOSE_WAIT_TIMEOUT_SECONDS=180
DEPLOY_DRAIN_SECONDS=0
NGINX_IMAGE_REPOSITORY=edge
target_slot=green
previous_slot=blue
docker() { printf '%s\\n' "$*"; }
sleep() { :; }
''' + functions + '''
stop_previous_slot blue
restore_previous_edge
printf 'RESTORED=%s\\n' "$I18N_ACTIVE_BACKEND"
previous_slot=''
restore_previous_edge
''')
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True, check=True)
        calls = result.stdout.splitlines()
        self.assertTrue(any('compose stop' in call and 'i18n-backend-blue' in call for call in calls))
        self.assertIn('RESTORED=i18n-backend-blue', calls)
        self.assertTrue(any('compose stop' in call and 'i18n-backend-green' in call for call in calls))
