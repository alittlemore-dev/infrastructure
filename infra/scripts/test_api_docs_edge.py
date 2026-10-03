#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    template = (ROOT / "infra/nginx/templates/site.conf.template").read_text()
    internal_server = template.split("# Schema-only HTTP listener:", 1)[1]
    internal_server = internal_server[internal_server.index("server {"):]
    public_locations = re.findall(
        r"    location (?:= /api/(?:docs|openapi\.json)|\^~ /api/docs/) \{\n.*?^    \}",
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
    limit_req_zone $binary_remote_addr zone=competency_api_per_ip:1m rate=120r/m;
    upstream auth_api_backend { server 127.0.0.1:8082; }
    upstream competency_backend { server 127.0.0.1:8082; }
    upstream personal_workspace_backend { server 127.0.0.1:8082; }
    upstream i18n_backend { server 127.0.0.1:8082; }
    upstream frontend { server 127.0.0.1:8082; }
    server { listen 8082; location / { return 200 "$uri"; } }
    server { listen 8081;
""" + "\n".join(public_locations) + "\n location / { return 404; }\n }\n" + internal_server + "\n}\n"
    image = re.search(r"^FROM (\S+)", (ROOT / "infra/nginx/Dockerfile").read_text(), re.MULTILINE)
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
                "--volume", f"{ROOT / 'infra/scripts/api_docs_edge_probe.sh'}:/tmp/probe.sh:ro",
                image.group(1), "/tmp/probe.sh",
            ],
            check=False,
            timeout=120,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
