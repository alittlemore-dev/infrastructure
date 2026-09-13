#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
import subprocess


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "infra/scripts"
sys.path.insert(0, str(SCRIPTS))

import quality_tools  # noqa: E402


def executable_bytes(version: str) -> bytes:
    return f"#!/bin/sh\nprintf '%s\\n' 'fake {version}'\n".encode()


def archive_bytes(member: str, payload: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        metadata = tarfile.TarInfo(member)
        metadata.mode = 0o755
        metadata.size = len(payload)
        archive.addfile(metadata, io.BytesIO(payload))
    return stream.getvalue()


def fake_spec(
    payload: bytes,
    *,
    archive_member: str | None = None,
) -> quality_tools.ToolSpec:
    artifact = quality_tools.Artifact(
        url="https://example.invalid/fake",
        sha256=hashlib.sha256(payload).hexdigest(),
        archive_member=archive_member,
    )
    return quality_tools.ToolSpec(
        name="fake",
        command="fake-tool",
        version="1.2.3",
        environment_variable="FAKE_BINARY",
        artifacts={
            ("linux", "amd64"): artifact,
            ("darwin", "arm64"): artifact,
        },
    )


class QualityToolsTest(unittest.TestCase):
    def test_sops_version_probe_disables_network_update_checks(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="sops 3.13.3\n",
            stderr="",
        )
        with mock.patch.object(quality_tools.subprocess, "run", return_value=completed) as run:
            self.assertTrue(
                quality_tools.has_expected_version(
                    Path("/tools/sops"),
                    quality_tools.TOOLS["sops"],
                )
            )

        self.assertEqual(
            ["/tools/sops", "--version", "--disable-version-check"],
            run.call_args.args[0],
        )

    def test_ensure_cli_reports_ready_tools_without_export_instructions(self) -> None:
        resolved = {
            "sops": Path("/tools/sops"),
            "age-keygen": Path("/tools/age-keygen"),
        }
        output = io.StringIO()
        with mock.patch.object(
            sys,
            "argv",
            ["quality_tools.py", "ensure", "--cache-dir", "/cache"],
        ), mock.patch.object(quality_tools, "ensure_tools", return_value=resolved), redirect_stdout(
            output
        ):
            result = quality_tools.main()

        self.assertEqual(0, result)
        self.assertEqual(
            [
                "sops 3.13.3: /tools/sops",
                "age-keygen 1.3.2: /tools/age-keygen",
            ],
            output.getvalue().splitlines(),
        )
        self.assertNotIn("_BINARY=", output.getvalue())

    def test_supported_release_artifacts_cover_local_platforms(self) -> None:
        expected_platforms = {
            ("linux", "amd64"),
            ("linux", "arm64"),
            ("darwin", "amd64"),
            ("darwin", "arm64"),
        }

        for name in ("sops", "age-keygen"):
            with self.subTest(tool=name):
                self.assertEqual(expected_platforms, set(quality_tools.TOOLS[name].artifacts))

    def test_resolve_reuses_an_exact_version_from_path(self) -> None:
        payload = executable_bytes("1.2.3")
        spec = fake_spec(payload)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            binary = root / spec.command
            binary.write_bytes(payload)
            binary.chmod(0o755)

            with mock.patch.dict(quality_tools.TOOLS, {spec.name: spec}):
                resolved = quality_tools.resolve_tool(
                    spec.name,
                    root / "cache",
                    environment={"PATH": str(root)},
                    system="Linux",
                    machine="x86_64",
                    downloader=lambda _url: self.fail("PATH tool should avoid downloads"),
                )

            self.assertEqual(binary, resolved)

    def test_wrong_path_version_falls_back_to_the_pinned_cache(self) -> None:
        wanted_payload = executable_bytes("1.2.3")
        spec = fake_spec(wanted_payload)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path_binary = root / "path" / spec.command
            path_binary.parent.mkdir()
            path_binary.write_bytes(executable_bytes("9.9.9"))
            path_binary.chmod(0o755)

            with mock.patch.dict(quality_tools.TOOLS, {spec.name: spec}):
                resolved = quality_tools.resolve_tool(
                    spec.name,
                    root / "cache",
                    environment={"PATH": str(path_binary.parent)},
                    system="Linux",
                    machine="x86_64",
                    downloader=lambda _url: wanted_payload,
                )

            self.assertNotEqual(path_binary, resolved)
            self.assertTrue(resolved.is_file())
            self.assertTrue(os.access(resolved, os.X_OK))

    def test_missing_explicit_destination_is_installed_there(self) -> None:
        payload = executable_bytes("1.2.3")
        spec = fake_spec(payload)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            explicit = root / "runner" / spec.command

            with mock.patch.dict(quality_tools.TOOLS, {spec.name: spec}):
                resolved = quality_tools.resolve_tool(
                    spec.name,
                    root / "cache",
                    environment={"FAKE_BINARY": str(explicit), "PATH": ""},
                    system="Darwin",
                    machine="arm64",
                    downloader=lambda _url: payload,
                )

            self.assertEqual(explicit, resolved)
            self.assertTrue(os.access(explicit, os.X_OK))

    def test_checksum_mismatch_never_replaces_the_destination(self) -> None:
        expected = executable_bytes("1.2.3")
        artifact = quality_tools.Artifact(
            url="https://example.invalid/fake",
            sha256=hashlib.sha256(expected).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "fake-tool"

            with self.assertRaisesRegex(quality_tools.ToolError, "checksum"):
                quality_tools.install_artifact(
                    artifact,
                    destination,
                    downloader=lambda _url: b"tampered",
                )

            self.assertFalse(destination.exists())

    def test_archive_install_extracts_only_the_declared_executable(self) -> None:
        executable = executable_bytes("1.2.3")
        archive = archive_bytes("age/age-keygen", executable)
        artifact = quality_tools.Artifact(
            url="https://example.invalid/age.tar.gz",
            sha256=hashlib.sha256(archive).hexdigest(),
            archive_member="age/age-keygen",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "age-keygen"

            quality_tools.install_artifact(
                artifact,
                destination,
                downloader=lambda _url: archive,
            )

            self.assertEqual(executable, destination.read_bytes())
            self.assertTrue(os.access(destination, os.X_OK))

    def test_unsupported_platform_fails_before_download(self) -> None:
        payload = executable_bytes("1.2.3")
        spec = fake_spec(payload)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(quality_tools.TOOLS, {spec.name: spec}):
                with self.assertRaisesRegex(quality_tools.ToolError, "unsupported platform"):
                    quality_tools.resolve_tool(
                        spec.name,
                        Path(temporary_directory),
                        environment={"PATH": ""},
                        system="Plan9",
                        machine="mips",
                        downloader=lambda _url: self.fail("unsupported platform downloaded data"),
                    )

    def test_test_runner_reports_all_missing_host_commands_before_running_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_tests.py"),
                    "--cache-dir",
                    str(Path(temporary_directory) / "cache"),
                ],
                cwd=ROOT,
                env={"PATH": ""},
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(1, result.returncode)
        self.assertIn("missing required commands: bash, make, ssh-keygen", result.stderr)


if __name__ == "__main__":
    unittest.main()
