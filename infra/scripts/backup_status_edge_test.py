#!/usr/bin/env python3
"""Actual nginx routing, SDK auth, slot switch and rollback without host ports."""
from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(args: list[str], timeout: int = 30) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


def check(image: str, projection: Path | None = None, output: Path | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="alm-backup-edge-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        name = root.name.lower()
        label = "alittlemore.backup-edge=" + name
        fixture, edge = name + "-fixture", name + "-nginx"
        if projection is not None:
            (root / "projection.json").write_bytes(projection.read_bytes())
        template = (ROOT / "infra/nginx/templates/site.conf.template").read_text()
        locations = re.findall(r"    location (?:= /api/infra/(?:healthcheck|backups/sets)|\^~ /api/infra/) \{\n.*?^    \}", template, re.MULTILINE | re.DOTALL)
        internal_match = re.search(r"server \{\n    listen 18085;.*?^\}", template, re.MULTILINE | re.DOTALL)
        if internal_match is None or len(locations) != 3:
            raise RuntimeError("Backup nginx routes are missing")
        internal = internal_match.group()
        nginx_match = re.search(r"^FROM (\S+)", (ROOT / "infra/nginx/Dockerfile").read_text(), re.MULTILINE)
        if nginx_match is None:
            raise RuntimeError("Nginx fixture image is missing")
        nginx = nginx_match.group(1)
        config = root / "nginx.conf"
        def configure(port: int) -> None:
            config.write_text("pid /tmp/nginx.pid; error_log /dev/stderr; events {} http { access_log off; "
                "proxy_temp_path /tmp/proxy; client_body_temp_path /tmp/client; fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi; "
                "upstream backup_status { server fixture:8080; } "
                f"upstream auth_api_backend {{ server fixture:{port}; }} "
                "server { listen 8081;\n" + "\n".join(locations) + "\nlocation / { return 404; }\n}\n" + internal + "\n}\n")
        configure(8082)
        owned = []
        network = None
        try:
            for resource, inspect in ((name, ["docker", "network", "inspect"]), (fixture, ["docker", "container", "inspect"]), (edge, ["docker", "container", "inspect"])):
                if subprocess.run([*inspect, resource], capture_output=True).returncode == 0:
                    raise RuntimeError("Refusing to reuse backup edge resources")
            network = run(["docker", "network", "create", "--label", label, name])
            owned.append(fixture)
            run(["docker", "run", "--detach", "--name", fixture, "--label", label, "--network", name, "--network-alias", "fixture",
                 "--cpus", "0.5", "--memory", "192m", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                 "--tmpfs", "/tmp:uid=10004,gid=10004", "--mount", f"type=bind,source={root},target=/fixture,readonly",
                 "--mount", f"type=bind,source={ROOT / 'infra/scripts/backup_status_edge_fixture.py'},target=/app/fixture.py,readonly",
                 "--entrypoint", "python", image, "/app/fixture.py", "serve"])
            ready = False
            for _ in range(30):
                result = subprocess.run(["docker", "exec", fixture, "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/live',timeout=1)"], capture_output=True)
                if result.returncode == 0:
                    ready = True
                    break
                time.sleep(0.2)
            if not ready:
                raise RuntimeError("Reader fixture failed: " + run(["docker", "logs", fixture]))
            owned.append(edge)
            run(["docker", "run", "--detach", "--name", edge, "--label", label, "--network", name, "--network-alias", "edge",
                 "--cpus", "0.5", "--memory", "64m", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                 "--tmpfs", "/tmp", "--mount", f"type=bind,source={root},target=/fixture,readonly",
                 "--entrypoint", "nginx", nginx, "-c", "/fixture/nginx.conf", "-g", "daemon off;"])
            ready = False
            for _ in range(30):
                result = subprocess.run(["docker", "exec", fixture, "python", "/app/fixture.py", "probe", "ready"], capture_output=True)
                if result.returncode == 0:
                    ready = True
                    break
                time.sleep(0.2)
            if not ready:
                logs = subprocess.run(["docker", "logs", edge], capture_output=True, text=True)
                raise RuntimeError("Nginx fixture failed: " + logs.stdout + logs.stderr + result.stdout.decode() + result.stderr.decode())
            run(["docker", "exec", fixture, "python", "/app/fixture.py", "probe"], 40)
            if output is not None:
                with output.open("wb") as stream:
                    subprocess.run(["docker", "exec", fixture, "cat", "/tmp/healthcheck-response.json"], stdout=stream, check=True, timeout=30)
            for port, mode in ((8083, "green"), (8082, "rollback")):
                configure(port)
                run(["docker", "exec", edge, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload"])
                run(["docker", "exec", fixture, "python", "/app/fixture.py", "probe", mode])
            print("nginx owner API, no-store, hidden paths, auth slot switch and rollback passed.")
        finally:
            for container in reversed(owned):
                cleanup_result = subprocess.run(["docker", "inspect", "--format", '{{index .Config.Labels "alittlemore.backup-edge"}}', container], capture_output=True, text=True)
                if cleanup_result.returncode == 0 and cleanup_result.stdout.strip() == name:
                    subprocess.run(["docker", "rm", "--force", container], capture_output=True, timeout=30)
            if network:
                cleanup_result = subprocess.run(["docker", "network", "inspect", "--format", '{{index .Labels "alittlemore.backup-edge"}}', network], capture_output=True, text=True)
                if cleanup_result.returncode == 0 and cleanup_result.stdout.strip() == name:
                    subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--projection", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    check(args.image, args.projection, args.output)
