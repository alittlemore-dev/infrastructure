#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    template = (ROOT / "infra/nginx/templates/site.conf.template").read_text()
    locations = re.findall(
        r"    location \^~ /api/personal-workspace/(?:internal/)? \{\n.*?^    \}",
        template,
        re.MULTILINE | re.DOTALL,
    )
    config = """pid /tmp/nginx.pid;
error_log /dev/stderr;
events {}
http {
    access_log off;
    proxy_temp_path /tmp/proxy_temp;
    client_body_temp_path /tmp/client_temp;
    fastcgi_temp_path /tmp/fastcgi_temp;
    uwsgi_temp_path /tmp/uwsgi_temp;
    scgi_temp_path /tmp/scgi_temp;
    limit_req_zone $binary_remote_addr zone=personal_api_per_ip:20m rate=120r/m;
    upstream personal_workspace_backend { server 127.0.0.1:8082; }
    server { listen 8082; location / { return 200 "public-or-internal-backend"; } }
    server { listen 8081;
""" + "\n".join(locations) + "\n} }\n"
    dockerfile = (ROOT / "infra/nginx/Dockerfile").read_text()
    image = re.search(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    if image is None:
        raise ValueError("nginx runtime image is missing")
    with tempfile.TemporaryDirectory() as temporary:
        config_file = Path(temporary) / "nginx.conf"
        config_file.write_text(config)
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--tmpfs", "/tmp", "--entrypoint", "sh",
                "--volume", f"{config_file}:/tmp/edge.conf:ro",
                "--volume", f"{ROOT / 'infra/scripts/telegram_edge_probe.sh'}:/tmp/probe.sh:ro",
                image.group(1), "/tmp/probe.sh",
            ],
            check=False,
            timeout=120,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
