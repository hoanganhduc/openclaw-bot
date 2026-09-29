"""Regression tests for OpenClaw skill and sandbox runtime contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_runtime_module(name: str, relative: str):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def encrypt_restore_fixture(
    root: Path, plaintext: Path, encrypted: Path
) -> dict[str, str]:
    """Encrypt an offline restore fixture through the production pinned-GPG path."""

    plaintext.chmod(0o600)
    passphrase = root / "fixture-passphrase"
    passphrase.write_text("offline-owner-fixture-passphrase\n", encoding="utf-8")
    passphrase.chmod(0o600)
    gnupg = root / "fixture-gnupg"
    gnupg.mkdir(mode=0o700, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/owner_archive.py"),
            "encrypt",
            "--source",
            str(plaintext),
            "--output",
            str(encrypted),
            "--passphrase-file",
            str(passphrase),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "GNUPGHOME": str(gnupg)},
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return {
        "GNUPGHOME": str(gnupg),
        "OPENCLAW_BACKUP_PASSPHRASE_FILE": str(passphrase),
    }


def frontmatter(relative: str) -> dict[str, object]:
    source = (ROOT / relative).read_text(encoding="utf-8")
    if not source.startswith("---\n"):
        raise AssertionError(f"{relative} has no YAML frontmatter")
    raw = source.split("---\n", 2)[1]
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise AssertionError(f"{relative} frontmatter is not a mapping")
    return parsed


class RuntimeContractTests(unittest.TestCase):
    def test_task_marker_never_stages_commits_resets_or_cleans(self) -> None:
        marker = (ROOT / "workspace/scripts/rollback_task.sh").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "git add",
            "git commit",
            "git reset",
            "git clean",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, marker)
        self.assertIn("Automatic rollback is disabled", marker)
        self.assertIn("git state unchanged", marker)

        instructions = (ROOT / "workspace/AGENTS.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("rollback_task.sh stop", instructions)
        self.assertIn("Do not reset, clean, discard", instructions)

    def test_moltbook_defaults_have_no_spawn_or_network_authority(self) -> None:
        template = json.loads(
            (ROOT / "config/openclaw.json.template").read_text(encoding="utf-8")
        )
        agents = {agent["id"]: agent for agent in template["agents"]["list"]}
        self.assertEqual(agents["main"]["subagents"]["allowAgents"], [])
        self.assertNotIn("heartbeat", agents["moltbook"])
        docker = agents["moltbook"]["sandbox"]["docker"]
        self.assertEqual(docker["network"], "none")
        self.assertEqual(docker["extraHosts"], [])
        self.assertNotIn("MOLTBOOK_AUTH", docker["env"])
        denied = set(agents["moltbook"]["tools"]["deny"])
        self.assertTrue({"group:runtime", "exec", "web_fetch"}.issubset(denied))
        self.assertNotIn("sandbox", agents["moltbook"]["tools"])
        self.assertNotIn("moltbook", template["browser"]["profiles"])

    def test_repaired_skills_have_valid_frontmatter(self) -> None:
        expected = {
            "workspace/skills/calibre/SKILL.md": "calibre",
            "workspace/skills/annotated-review/SKILL.md": "annotated-review",
            "workspace/skills/vnu-eoffice/SKILL.md": "vnu-eoffice",
        }
        for relative, name in expected.items():
            with self.subTest(relative=relative):
                metadata = frontmatter(relative)
                self.assertEqual(metadata.get("name"), name)
                self.assertIsInstance(metadata.get("description"), str)

    def test_sage_inline_job_uses_container_workspace_path(self) -> None:
        source = (
            ROOT / "workspace/scripts/job_queue_worker.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'source_path="data/job-queue/${job_id}.sage"', source
        )
        self.assertIn(
            'container_sage="$(basename -- "$sage_snapshot")"',
            source,
        )
        self.assertIn('< "$sage_snapshot"', source)
        self.assertNotIn("PRIVATE_DATA_DIR", source)

    def test_sage_container_mounts_only_the_job_queue(self) -> None:
        source = (
            ROOT / "workspace/scripts/job_queue_worker.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('-v "$JOB_QUEUE:/workspace/data/job-queue"', source)
        self.assertNotIn('-v "$WORKSPACE:/workspace"', source)
        self.assertNotIn("/opt/openclaw-jobs", source)
        self.assertIn('chmod 1777 "$JOB_QUEUE"', source)

    def test_sage_container_has_writable_dot_sage(self) -> None:
        source = (
            ROOT / "workspace/scripts/job_queue_worker.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("-e DOT_SAGE=/tmp/.sage", source)
        self.assertIn('grep -Fqx "DOT_SAGE=/tmp/.sage"', source)

    def test_sage_stdin_runner_runs_a_private_copy_and_keeps_exit_status(self) -> None:
        source = (
            ROOT / "workspace/scripts/job_queue_worker.sh"
        ).read_text(encoding="utf-8")
        match = re.search(r"^SAGE_STDIN_RUNNER='([^']+)'$", source, re.MULTILINE)
        self.assertIsNotNone(match)
        assert match is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_sage = fake_bin / "sage"
            fake_sage.write_text(
                "#!/bin/sh\n"
                'printf "%s" "$1" > "$OUT/argument"\n'
                'cat "$1" > "$OUT/content"\n'
                "exit 3\n",
                encoding="utf-8",
            )
            fake_sage.chmod(0o700)
            result = subprocess.run(
                ["/bin/sh", "-c", match.group(1), "openclaw-sage", "job-1-abc.sage"],
                input=b"print(1)\n",
                env={"PATH": f"{fake_bin}:/usr/bin:/bin", "OUT": str(root)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 3, result.stderr)
            argument = Path((root / "argument").read_text(encoding="utf-8"))
            self.assertTrue(str(argument.parent).startswith("/tmp/openclaw-sage."))
            self.assertEqual(argument.name, "job-1-abc.sage")
            self.assertEqual((root / "content").read_bytes(), b"print(1)\n")
            self.assertFalse(argument.parent.exists())

    def test_shared_queue_worker_is_neutral_runtime_infrastructure(self) -> None:
        worker_source = (ROOT / "workspace/scripts/job_queue_worker.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('DELIVERY_HELPER="$LIBEXEC/file_delivery.py"', worker_source)
        self.assertNotIn("--authorize-only", worker_source)
        service = (ROOT / "systemd/user/send-queue-worker.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("{{ OPENCLAW_LIBEXEC }}/host_exec.py", service)
        self.assertIn("--artifact job_queue_worker.sh", service)
        self.assertNotIn("{{ OPENCLAW_WORKSPACE }}/scripts/job_queue_worker.sh", service)
        self.assertNotIn("/skills/zotero/job_queue_worker.sh", service)
        for name in ("job_queue_worker.sh", "send_queue_worker.sh"):
            compatibility = (
                ROOT / "workspace/skills/zotero" / name
            ).read_text(encoding="utf-8")
            self.assertIn("../../scripts/job_queue_worker.sh", compatibility)
            self.assertNotIn("python3 -c", compatibility)

    def test_queue_worker_accepts_dot_prefixed_state_lock(self) -> None:
        worker = ROOT / "workspace/scripts/job_queue_worker.sh"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            state = root / ".openclaw"
            libexec = root / "libexec"
            lock = root / ".openclaw.owner-state.lock"
            temp = root / "tmp"
            for directory in (workspace, state, libexec, temp):
                directory.mkdir(mode=0o700)
            lock.write_text("", encoding="utf-8")
            lock.chmod(0o600)
            environment = {
                "HOME": str(root),
                "OPENCLAW_EXPECTED_OWNER_STATE_LOCK": str(lock),
                "OPENCLAW_LIBEXEC": str(libexec),
                "OPENCLAW_OWNER_STATE_LOCK": str(lock),
                "OPENCLAW_QUEUE_KIND": "sage",
                "OPENCLAW_STATE_DIR": str(state),
                "OPENCLAW_WORKSPACE": str(workspace),
                "PATH": "/usr/bin:/bin",
                "TMPDIR": str(temp),
            }
            result = subprocess.run(
                ["/usr/bin/timeout", "1", "/usr/bin/bash", "-p", str(worker)],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 124, result.stderr)
            self.assertNotIn("owner-state lock", result.stderr)

    def _legacy_file_delivery_requires_authorized_export_and_exact_target(self) -> None:
        helper = ROOT / "scripts/file_delivery.py"
        sender = ROOT / "workspace/skills/zotero/send_file.sh"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            staging = workspace / "data/research/zotero/staging"
            staging.mkdir(parents=True, mode=0o700)
            workspace.chmod(0o700)
            staging.chmod(0o700)
            artifact = staging / "paper.pdf"
            artifact.write_bytes(b"approved-export")
            artifact.chmod(0o600)
            private_file = root / "private-secrets.json"
            private_file.write_text('{"credential":"private-canary"}\n')
            private_file.chmod(0o600)

            home = root / "home"
            policy_directory = home / ".config/file-delivery"
            policy_directory.mkdir(parents=True, mode=0o700)
            policy_directory.chmod(0o700)
            policy = policy_directory / "secrets.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema": "openclaw.file-delivery-policy/v1",
                        "TELEGRAM_BOT_TOKEN": "offline-token-canary",
                        "delivery_policy": {
                            "allowed_targets": {
                                "telegram": ["approved-chat"],
                                "zulip": [],
                                "googlechat": [],
                                "whatsapp": [],
                                "zalo": [],
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            policy.chmod(0o600)

            base = [
                sys.executable,
                str(helper),
                "--workspace",
                str(workspace),
                "--policy",
                str(policy),
                "--channel",
                "telegram",
            ]
            wrong_target = subprocess.run(
                [
                    *base,
                    "--target",
                    "attacker-chat",
                    "--file",
                    str(artifact),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(wrong_target.returncode, 2)
            self.assertIn("target is not authorized", wrong_target.stderr)

            arbitrary_file = subprocess.run(
                [
                    *base,
                    "--target",
                    "approved-chat",
                    "--file",
                    str(private_file),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(arbitrary_file.returncode, 2)
            self.assertIn("outside authorized delivery export roots", arbitrary_file.stderr)

            linked = staging / "linked-private.json"
            linked.symlink_to(private_file)
            linked_file = subprocess.run(
                [
                    *base,
                    "--target",
                    "approved-chat",
                    "--file",
                    str(linked),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(linked_file.returncode, 2)

            authorized = subprocess.run(
                [
                    *base,
                    "--target",
                    "approved-chat",
                    "--file",
                    str(artifact),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(authorized.returncode, 0, authorized.stderr)
            authorization = json.loads(authorized.stdout)
            snapshot = Path(authorization["snapshot"])
            snapshot_directory = Path(authorization["snapshotDirectory"])
            self.assertEqual(snapshot.read_bytes(), b"approved-export")
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
            reauthorized = subprocess.run(
                [
                    *base,
                    "--target",
                    "approved-chat",
                    "--file",
                    str(snapshot),
                    "--authorize-only",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(reauthorized.returncode, 0, reauthorized.stderr)
            snapshot.unlink()
            snapshot_directory.rmdir()

            control = workspace / "_control"
            control.mkdir(mode=0o700)
            shutil.copy2(ROOT / "scripts/owner_state_lock.py", control)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_curl_canary = root / "attacker-curl-ran"
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                f"#!/bin/sh\ntouch {fake_curl_canary}\nexit 91\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o700)
            sender_result = subprocess.run(
                [
                    "/usr/bin/bash",
                    str(sender),
                    "telegram",
                    "attacker-chat",
                    str(private_file),
                    "blocked",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "OPENCLAW_STATE_DIR": str(root / "state"),
                    "OPENCLAW_WORKSPACE": str(workspace),
                    "AAS_RUNTIME_WORKSPACE": str(workspace),
                    "AAS_FILE_DELIVERY_SECRETS_FILE": str(policy),
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )
            self.assertEqual(sender_result.returncode, 2, sender_result.stderr)
            self.assertFalse(fake_curl_canary.exists())
            source = sender.read_text(encoding="utf-8")
            self.assertIn("CURL_BIN=/usr/bin/curl", source)
            self.assertNotIn("| curl ", source)
            self.assertNotIn("['curl'", source)

    def _legacy_file_delivery_policy_rejects_noncanonical_documents(self) -> None:
        helper = ROOT / "scripts/file_delivery.py"
        schema = "openclaw.file-delivery-policy/v1"

        def valid_document() -> dict[str, object]:
            return {
                "schema": schema,
                "delivery_policy": {
                    "allowed_targets": {
                        "telegram": ["approved-chat"],
                        "zulip": [],
                        "googlechat": [],
                        "whatsapp": [],
                        "zalo": [],
                    }
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            staging = workspace / "data/research/zotero/staging"
            staging.mkdir(parents=True, mode=0o700)
            workspace.chmod(0o700)
            staging.chmod(0o700)
            artifact = staging / "paper.pdf"
            artifact.write_bytes(b"approved-export")
            artifact.chmod(0o600)
            policy_directory = root / "policy"
            policy_directory.mkdir(mode=0o700)
            policy = policy_directory / "secrets.json"

            unknown = valid_document()
            unknown["ZULIP_API_KEY"] = "must-not-cross-policy-boundary"
            mismatched = valid_document()
            mismatched["schema"] = "openclaw.file-delivery-policy/v0"
            missing_channel = valid_document()
            del missing_channel["delivery_policy"]["allowed_targets"]["zalo"]
            empty_target = valid_document()
            empty_target["delivery_policy"]["allowed_targets"]["telegram"] = [""]
            control_target = valid_document()
            control_target["delivery_policy"]["allowed_targets"]["telegram"] = [
                "approved-chat\nattacker-chat"
            ]
            duplicate_target = valid_document()
            duplicate_target["delivery_policy"]["allowed_targets"]["telegram"] = [
                "approved-chat",
                "approved-chat",
            ]
            empty_token = valid_document()
            empty_token["TELEGRAM_BOT_TOKEN"] = ""
            duplicate_key = (
                '{"schema":"openclaw.file-delivery-policy/v1",'
                '"schema":"openclaw.file-delivery-policy/v1",'
                '"delivery_policy":{"allowed_targets":{'
                '"telegram":["approved-chat"],"zulip":[],"googlechat":[],'
                '"whatsapp":[],"zalo":[]}}}'
            )
            cases = {
                "unknown-top-level": json.dumps(unknown),
                "mismatched-schema": json.dumps(mismatched),
                "missing-channel": json.dumps(missing_channel),
                "empty-target": json.dumps(empty_target),
                "control-target": json.dumps(control_target),
                "duplicate-target": json.dumps(duplicate_target),
                "empty-optional-token": json.dumps(empty_token),
                "duplicate-json-key": duplicate_key,
            }
            for name, payload in cases.items():
                with self.subTest(name=name):
                    policy.write_text(payload + "\n", encoding="utf-8")
                    policy.chmod(0o600)
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(helper),
                            "--workspace",
                            str(workspace),
                            "--policy",
                            str(policy),
                            "--channel",
                            "telegram",
                            "--target",
                            "approved-chat",
                            "--file",
                            str(artifact),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("delivery authorization", result.stderr.casefold())

    def test_portable_file_delivery_policy_contract_is_deny_by_default(self) -> None:
        schema = "openclaw.file-delivery-policy/v1"
        channels = {"telegram", "zulip", "googlechat", "whatsapp", "zalo"}
        template = json.loads(
            (ROOT / "config/file-delivery-policy.json.template").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(template["schema"], schema)
        self.assertEqual(set(template), {"schema", "delivery_policy"})
        self.assertNotIn("TELEGRAM_BOT_TOKEN", template)
        targets = template["delivery_policy"]["allowed_targets"]
        self.assertEqual(set(targets), channels)
        self.assertTrue(all(value == [] for value in targets.values()))

        host_secrets = json.loads(
            (ROOT / "config/secrets.json.template").read_text(encoding="utf-8")
        )
        self.assertIn("TELEGRAM_CHAT_ID", host_secrets)
        self.assertEqual(host_secrets["TELEGRAM_CHAT_ID"], "")

        manifest = json.loads(
            (ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8")
        )
        archive = manifest["owner_archive"]
        self.assertEqual(
            archive["file_delivery_policy_authority"],
            "file-delivery-policy.json",
        )
        self.assertEqual(archive["file_delivery_policy_schema"], schema)
        self.assertEqual(archive["file_delivery_restore_authority"], "owner-archive-only")
        self.assertEqual(archive["file_delivery_generic_secrets_restore"], "omitted")
        self.assertIn("file-delivery-policy.json", manifest["private_archive"])
        workspace = next(
            item
            for item in manifest["classifications"]
            if item.get("source") == "workspace" and item.get("dest") == "workspace"
        )
        self.assertIn(".config/**", workspace["exclude"])

        owner_archive = (ROOT / "scripts/owner_archive.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('FILE_DELIVERY_POLICY = "file-delivery-policy.json"', owner_archive)
        self.assertIn(
            "file-delivery policy authority is not owner-private", owner_archive
        )
        archive_spec = importlib.util.spec_from_file_location(
            "owner_archive_policy_contract",
            ROOT / "scripts/owner_archive.py",
        )
        assert archive_spec is not None and archive_spec.loader is not None
        archive_module = importlib.util.module_from_spec(archive_spec)
        archive_spec.loader.exec_module(archive_module)
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            state.mkdir(mode=0o700)
            authority = state / "file-delivery-policy.json"
            authority.write_text(json.dumps(template) + "\n", encoding="utf-8")
            authority.chmod(0o600)
            selected = {
                relative.as_posix()
                for _path, relative in archive_module._iter_live_paths(state)
            }
            self.assertIn("file-delivery-policy.json", selected)
            stored = archive_module._stored_relative(
                archive_module.PurePosixPath("file-delivery-policy.json")
            )
            self.assertEqual(
                stored.as_posix(),
                "recovery-quarantine/archive-authority/payload/"
                "file-delivery-policy.json",
            )
            authority.chmod(0o644)
            state_descriptor = archive_module._open_directory_nofollow(state)
            try:
                with self.assertRaisesRegex(
                    archive_module.ArchiveError,
                    "file-delivery policy authority is not owner-private",
                ):
                    archive_module._add_live_file(
                        object(),
                        state_descriptor,
                        archive_module.PurePosixPath("file-delivery-policy.json"),
                    )
            finally:
                os.close(state_descriptor)
        sender = (ROOT / "workspace/skills/zotero/send_file.sh").read_text(
            encoding="utf-8"
        )
        worker = (ROOT / "workspace/scripts/job_queue_worker.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".config/file-delivery", sender)
        self.assertNotIn(".config/file-delivery", worker)
        self.assertIn("data/send-queue", sender)
        self.assertIn('DELIVERY_HELPER="$LIBEXEC/file_delivery.py"', worker)
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("file-delivery-policy.json.template", installer)
        synchronizer = (ROOT / "sync.sh").read_text(encoding="utf-8")
        self.assertIn('"config/file-delivery-policy.json.template"', synchronizer)
        self.assertIn(
            'REQUIRED_HOST_SECRET_TEMPLATE_KEYS = {"TELEGRAM_CHAT_ID"}',
            synchronizer,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            repository = root / "repository"
            home = root / "home"
            for directory in (state, repository, home):
                directory.mkdir(mode=0o700)
            live_secrets = state / "secrets.json"
            live_secrets.write_text(
                '{"TELEGRAM_BOT_TOKEN":"redacted-fixture"}\n',
                encoding="utf-8",
            )
            live_secrets.chmod(0o600)
            minimal_manifest = repository / "REBUILD-MANIFEST.json"
            minimal_manifest.write_text(
                json.dumps(
                    {
                        "classifications": [
                            {
                                "base": "openclaw",
                                "source": "secrets.json",
                                "dest": "config/secrets.json.template",
                                "class": "public-template",
                                "mode": "file",
                                "template": "secrets-keys",
                            }
                        ],
                        "release_checks": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            minimal_manifest.chmod(0o600)
            staging = root / "staging"
            generated = subprocess.run(
                [
                    str(ROOT / "sync.sh"),
                    "--prefix",
                    str(state),
                    "--repo",
                    str(repository),
                    "--manifest",
                    str(minimal_manifest),
                    "--staging",
                    str(staging),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "HOME": str(home)},
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            generated_secrets = json.loads(
                (staging / "config/secrets.json.template").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                generated_secrets,
                {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""},
            )

    def test_gateway_service_binds_the_selected_owner_state_prefix(self) -> None:
        service = (ROOT / "systemd/user/openclaw-gateway.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("Environment=OPENCLAW_STATE_DIR={{ OPENCLAW_HOME }}", service)
        self.assertIn(
            "Environment=OPENCLAW_CONFIG_PATH={{ OPENCLAW_HOME }}/openclaw.json",
            service,
        )
        self.assertIn("{{ OPENCLAW_LIBEXEC }}/host_exec.py", service)
        self.assertIn("--artifact owner_state_lock.py -- --mode shared", service)
        self.assertIn("--lock-path {{ OWNER_STATE_LOCK }}", service)
        for relative in (
            "systemd/user/moltbook-relay.service",
            "systemd/user/rss_news_digest_bot.service",
        ):
            writer_service = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("{{ OPENCLAW_LIBEXEC }}/host_exec.py", writer_service)
            self.assertIn("--artifact owner_state_lock.py -- --mode shared", writer_service)
            self.assertIn("--lock-path {{ OWNER_STATE_LOCK }}", writer_service)

    def test_reviewed_service_install_is_opt_in_and_rolls_back_reload_failure(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        service = (ROOT / "systemd/user/openclaw-gateway.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("SKIP_SERVICES=1", installer)
        self.assertIn("INSTALL_REVIEWED_USER_SERVICES", installer)
        self.assertIn("OPENCLAW_SERVICE_VERSION=2026.7.1-2", service)

        specification = importlib.util.spec_from_file_location(
            "service_transaction_test", ROOT / "scripts/service_transaction.py"
        )
        assert specification is not None and specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        self.assertEqual(
            module._owner_state_lock_path(Path("/tmp/.openclaw")),
            Path("/tmp/.openclaw.owner-state.lock"),
        )
        self.assertEqual(
            module._owner_state_lock_path(Path("/tmp/state")),
            Path("/tmp/.state.owner-state.lock"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir(mode=0o700)
            destination.mkdir(mode=0o700)
            unit = source / "fixture.service"
            unit.write_text(
                "[Service]\n"
                "ExecStart=/usr/bin/python3 -I -S -B {{ OPENCLAW_LIBEXEC }}/host_exec.py "
                "--generation {{ OPENCLAW_LIBEXEC }} --artifact fixture.py\n"
                "Environment=LOCK={{ OWNER_STATE_LOCK }}\n",
                encoding="utf-8",
            )
            unit.chmod(0o600)
            installed = destination / "fixture.service"
            installed.write_text("original-unit\n", encoding="utf-8")
            installed.chmod(0o600)
            obsolete = (
                destination
                / "openclaw-gateway.service.d"
                / "10-moltbook-env.conf"
            )
            obsolete.parent.mkdir(mode=0o700)
            obsolete.write_text("obsolete-drop-in\n", encoding="utf-8")
            obsolete.chmod(0o600)
            with mock.patch.object(
                module,
                "reload_services",
                side_effect=module.ServiceTransactionError("reload canary"),
            ):
                with self.assertRaises(module.ServiceTransactionError):
                    module._install_reviewed_files(
                        source,
                        destination,
                        prefix=root / "state",
                        home=root,
                        libexec=root / "libexec",
                        reload=True,
                        dry_run=False,
                    )
            self.assertEqual(installed.read_text(), "original-unit\n")
            self.assertEqual(obsolete.read_text(), "obsolete-drop-in\n")

            with mock.patch.object(module, "reload_services") as reload_mock, mock.patch.object(
                module,
                "restart_active_services",
                side_effect=module.ServiceTransactionError("restart canary"),
            ) as restart_mock:
                with self.assertRaises(module.ServiceTransactionError):
                    module._install_reviewed_files(
                        source,
                        destination,
                        prefix=root / "state",
                        home=root,
                        libexec=root / "libexec",
                        reload=True,
                        dry_run=False,
                    )
            self.assertEqual(reload_mock.call_count, 2)
            self.assertEqual(restart_mock.call_count, 2)
            self.assertEqual(installed.read_text(), "original-unit\n")
            self.assertEqual(obsolete.read_text(), "obsolete-drop-in\n")

            with mock.patch.object(module, "reload_services") as reload_mock, mock.patch.object(
                module, "restart_active_services"
            ) as restart_mock:
                module._install_reviewed_files(
                    source,
                    destination,
                    prefix=root / "state",
                    home=root,
                    libexec=root / "libexec",
                    reload=True,
                    dry_run=False,
                )
            reload_mock.assert_called_once_with(root)
            restart_mock.assert_called_once_with(root, ("fixture.service",))
            self.assertFalse(obsolete.exists())

            external = root / "external-unit"
            external.write_text("external-canary\n", encoding="utf-8")
            installed.unlink()
            installed.symlink_to(external)
            with self.assertRaises(OSError):
                module._install_reviewed_files(
                    source,
                    destination,
                    prefix=root / "state",
                    home=root,
                    libexec=root / "libexec",
                    reload=False,
                    dry_run=False,
                )
            self.assertEqual(external.read_text(), "external-canary\n")

            public_helper = ROOT / "scripts/service_transaction.py"
            arbitrary = subprocess.run(
                [
                    sys.executable,
                    str(public_helper),
                    "--installer-fd",
                    "0",
                    "--source",
                    str(source),
                    "--destination",
                    str(destination),
                    "--prefix",
                    str(root / "state"),
                    "--home",
                    str(root),
                    "--no-reload",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(arbitrary.returncode, 2)
            self.assertIn("unrecognized arguments", arbitrary.stderr)

            forged_installer = root / "install.sh"
            forged_installer.write_text("#!/usr/bin/bash\n", encoding="utf-8")
            forged_installer.chmod(0o700)
            forged_descriptor = os.open(forged_installer, os.O_RDONLY)
            try:
                forged = subprocess.run(
                    [
                        sys.executable,
                        str(public_helper),
                        "--installer-fd",
                        str(forged_descriptor),
                        "--prefix",
                        str(root / "state"),
                        "--dry-run",
                    ],
                    pass_fds=(forged_descriptor,),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            finally:
                os.close(forged_descriptor)
            self.assertEqual(forged.returncode, 2)
            self.assertIn("invalid reviewed-installer capability", forged.stderr)

    def test_tikz_runner_exposes_workspace_local_packages(self) -> None:
        source = (ROOT / "workspace/skills/tikz-draw/run_tikz_draw.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("$WORKSPACE_ROOT/.local", source)

    def test_tikz_references_are_relative(self) -> None:
        source = (ROOT / "workspace/skills/tikz-draw/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("<HOME>/.codex/skills/tikz-draw/references", source)
        for name in (
            "backend-routing.md",
            "quality-gates.md",
            "tikz-prevention.md",
            "tikz-measurement.md",
        ):
            self.assertIn(f"(references/{name})", source)
            self.assertTrue((ROOT / "workspace/skills/tikz-draw/references" / name).is_file())

    def test_browser_template_uses_an_existing_profile(self) -> None:
        source = (ROOT / "config/openclaw.json.template").read_text(encoding="utf-8")
        self.assertIn('"defaultProfile": "openclaw"', source)

    def test_getscipapers_helper_uses_a_workspace_portable_launcher(self) -> None:
        helper = (
            ROOT / "workspace/skills/getscipapers_requester/gsp_openclaw_helper.py"
        ).read_text(encoding="utf-8")
        runner = (
            ROOT / "workspace/skills/getscipapers_requester/run_gsp_helper.sh"
        ).read_text(encoding="utf-8")
        launcher = (
            ROOT / "workspace/skills/getscipapers_requester/run_getscipapers.sh"
        )
        self.assertIn('os.environ.get("GETSCIPAPERS_BIN")', helper)
        self.assertIn("run_getscipapers.sh", runner)
        self.assertTrue(launcher.is_file())
        launcher_source = launcher.read_text(encoding="utf-8")
        self.assertIn('ENTRYPOINT="/usr/local/bin/getscipapers"', launcher_source)
        self.assertIn("python-closure/getscipapers", launcher_source)
        self.assertIn('PYTHON="$VENV/bin/python"', launcher_source)
        self.assertIn('ENTRYPOINT="$VENV/bin/getscipapers"', launcher_source)
        root_launcher = (
            ROOT / "root-skills/getscipapers_requester/run_getscipapers.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('ENTRYPOINT="/usr/local/bin/getscipapers"', root_launcher)
        self.assertIn("python-closure/getscipapers", root_launcher)

    def test_docling_and_lean_explore_use_prebuilt_closures(self) -> None:
        docling = (
            ROOT / "workspace/skills/docling/run_docling.sh"
        ).read_text(encoding="utf-8")
        lean = (
            ROOT / "workspace/skills/lean-explore-cli/run_lean_explore.sh"
        ).read_text(encoding="utf-8")
        for source, environment, sandbox_predicate in (
            (docling, "docling-cpu", '"$HOME" == "/workspace"'),
            (lean, "lean-explore", '"$WS" == "/workspace"'),
        ):
            with self.subTest(environment=environment):
                self.assertIn(
                    f"/opt/coding-system/python-closure/{environment}", source
                )
                self.assertIn(
                    f".local/share/coding-system/python-closure/{environment}",
                    source,
                )
                self.assertIn(sandbox_predicate, source)
                self.assertNotRegex(source, r"pip\s+install|python3\s+-m\s+venv")

    def test_workspace_python_closure_is_not_captured_as_source(self) -> None:
        manifest = json.loads((ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8"))
        workspace = next(
            item
            for item in manifest["classifications"]
            if item.get("source") == "workspace" and item.get("dest") == "workspace"
        )
        self.assertIn(".python-closure/**", workspace["exclude"])
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn(".python-closure/", install)
        self.assertIn('workspace_exclude="$DESTINATION_WORKSPACE/.git/info/exclude"', install)

    def test_openclaw_and_plugin_versions_are_one_locked_generation(self) -> None:
        manifest = json.loads((ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["openclaw"]["observed_version"], "2026.7.1-2")
        expected = {
            "openclaw-channel-zulip": "2026.5.26",
            "@openclaw/codex": "2026.7.1-1",
            "@openclaw/deepseek-provider": "2026.7.1",
            "@openclaw/googlechat": "2026.7.1",
            "@openclaw/groq-provider": "2026.7.1",
            "@openclaw/whatsapp": "2026.7.1",
            "@openclaw/zai-provider": "2026.7.1",
            "@openclaw/zalo": "2026.7.1",
            "sharp": "0.34.5",
        }
        observed = {}
        for package_json in (ROOT / "npm/projects").glob("*/package.json"):
            package = json.loads(package_json.read_text(encoding="utf-8"))
            observed.update(package.get("dependencies", {}))
            self.assertTrue(package_json.with_name("package-lock.json").is_file())
        self.assertEqual(observed, expected)

    def test_template_uses_least_privilege_and_bounded_sandboxes(self) -> None:
        config = json.loads(
            (ROOT / "config/openclaw.json.template").read_text(encoding="utf-8")
        )
        self.assertFalse(config["tools"]["elevated"]["enabled"])
        self.assertEqual(config["tools"]["exec"]["host"], "sandbox")
        self.assertEqual(config["tools"]["exec"]["security"], "allowlist")
        self.assertEqual(config["tools"]["exec"]["ask"], "on-miss")
        self.assertTrue(config["tools"]["exec"]["strictInlineEval"])
        default_docker = config["agents"]["defaults"]["sandbox"]["docker"]
        for agent in [config["agents"]["defaults"], *config["agents"]["list"]]:
            docker = {**default_docker, **agent.get("sandbox", {}).get("docker", {})}
            self.assertIn("@sha256:", docker["image"])
            self.assertEqual(docker["pidsLimit"], 512)
            self.assertEqual(docker["memory"], "4g")
            self.assertEqual(docker["memorySwap"], "4g")
            self.assertEqual(docker["cpus"], 2)

    def test_installer_materializes_npm_projects_convergently(self) -> None:
        source = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('render_copy "$SCRIPT_DIR/npm" "$DESTINATION_PREFIX/npm"', source)
        self.assertIn("--destination-prefix", source)
        self.assertIn("CONVERGENT", source)
        self.assertNotIn('preview = path.with_name(path.name + ".new")', source)
        self.assertNotIn('render_copy "$SCRIPT_DIR/agents"', source)
        self.assertNotIn("auth-profiles.json", source)
        self.assertIn('-c user.name="OpenClaw Restore"', source)
        self.assertIn('-c user.email="openclaw-restore@localhost"', source)
        self.assertNotIn(
            'commit -m "Initialize OpenClaw workspace rollback baseline" >/dev/null || true',
            source,
        )

    def test_installer_stages_bytes_with_final_logical_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logical = root / "active-state"
            stage = root / ".active-state.restore-stage.fixture"
            home = root / "home"
            home.mkdir()
            completed = subprocess.run(
                [
                    str(ROOT / "install.sh"),
                    "--prefix",
                    str(logical),
                    "--destination-prefix",
                    str(stage),
                    "--skip-config",
                    "--skip-docker",
                    "--skip-services",
                    "--skip-openclaw-install",
                    "--convergent",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "HOME": str(home)},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(logical.exists())
            runner = (stage / "workspace/skills/_run.sh").read_text(encoding="utf-8")
            self.assertIn(str(logical / "workspace"), runner)
            self.assertNotIn(str(stage), runner)
            review_queue = stage / "workspace-review/data/review-queue"
            self.assertTrue(review_queue.is_symlink())
            self.assertEqual(
                os.readlink(review_queue), str(logical / "workspace/data/review-queue")
            )

    def test_installer_strips_group_and_world_write_from_managed_files(self) -> None:
        source = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("& ~0o022"), 2)

    def test_sync_rejects_final_nested_and_intermediate_source_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "state"
            repository = root / "repo"
            home = root / "home"
            for directory in (prefix, repository, home):
                directory.mkdir(mode=0o700)

            outside_file = root / "outside.txt"
            outside_file.write_text("outside-canary\n", encoding="utf-8")
            final_link = prefix / "final-link.txt"
            final_link.symlink_to(outside_file)

            tree = prefix / "tree"
            tree.mkdir(mode=0o700)
            (tree / "safe.txt").write_text("safe\n", encoding="utf-8")
            (tree / "excluded-link").symlink_to(outside_file)

            outside_tree = root / "outside-tree"
            outside_tree.mkdir(mode=0o700)
            (outside_tree / "nested").mkdir(mode=0o700)
            (outside_tree / "nested/value.txt").write_text("outside\n")
            (prefix / "linked-parent").symlink_to(outside_tree)

            cases = (
                {"source": "final-link.txt", "dest": "out.txt", "mode": "file"},
                {
                    "source": "linked-parent/nested",
                    "dest": "nested",
                    "mode": "tree",
                    "include": ["**/*"],
                },
            )
            for index, classification in enumerate(cases):
                with self.subTest(classification=classification):
                    manifest = repository / "REBUILD-MANIFEST.json"
                    manifest.write_text(
                        json.dumps(
                            {
                                "classifications": [
                                    {
                                        "class": "public-copy",
                                        "base": "openclaw",
                                        **classification,
                                    }
                                ],
                                "release_checks": {},
                            }
                        ),
                        encoding="utf-8",
                    )
                    manifest.chmod(0o600)
                    result = subprocess.run(
                        [
                            str(ROOT / "sync.sh"),
                            "--prefix",
                            str(prefix),
                            "--repo",
                            str(repository),
                            "--manifest",
                            str(manifest),
                            "--staging",
                            str(root / f"staging-{index}"),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        env={**os.environ, "HOME": str(home)},
                    )
                    self.assertEqual(result.returncode, 1)
                    diagnostic = result.stderr.casefold()
                    self.assertTrue(
                        "symlink" in diagnostic
                        or "not a directory" in diagnostic
                        or "unsafe" in diagnostic
                    )
            self.assertEqual(outside_file.read_text(), "outside-canary\n")

            excluded_manifest = repository / "REBUILD-MANIFEST.json"
            excluded_manifest.write_text(
                json.dumps(
                    {
                        "classifications": [
                            {
                                "class": "public-copy",
                                "base": "openclaw",
                                "source": "tree",
                                "dest": "tree",
                                "mode": "tree",
                                "include": ["safe.txt"],
                                "exclude": ["excluded-link"],
                            }
                        ],
                        "release_checks": {},
                    }
                ),
                encoding="utf-8",
            )
            excluded_manifest.chmod(0o600)
            excluded_result = subprocess.run(
                [
                    str(ROOT / "sync.sh"),
                    "--prefix",
                    str(prefix),
                    "--repo",
                    str(repository),
                    "--manifest",
                    str(excluded_manifest),
                    "--staging",
                    str(root / "excluded-staging"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "HOME": str(home)},
            )
            self.assertEqual(excluded_result.returncode, 0, excluded_result.stderr)
            self.assertEqual(
                (root / "excluded-staging/tree/safe.txt").read_text(), "safe\n"
            )
            self.assertFalse(
                (root / "excluded-staging/tree/excluded-link").exists()
            )

    def test_owner_backup_is_link_free_and_restore_is_bounded(self) -> None:
        backup = (ROOT / "backup.sh").read_text(encoding="utf-8")
        restore = (ROOT / "restore.sh").read_text(encoding="utf-8")
        helper = (ROOT / "scripts/owner_archive.py").read_text(encoding="utf-8")
        manifest = json.loads(
            (ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8")
        )
        self.assertIn(
            '/usr/bin/node "$OPENCLAW_CLI" backup create --no-include-workspace --verify',
            backup,
        )
        self.assertIn("owner_archive.py", backup)
        self.assertIn("transactional-backup-only", helper)
        self.assertIn("openclaw.owner-archive/v5", helper)
        self.assertIn("excludedActionQueues", helper)
        self.assertIn("member.issym()", helper)
        self.assertIn("member.islnk()", helper)
        self.assertIn("MAX_MEMBERS = 100_000", helper)
        self.assertIn("MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024", helper)
        self.assertIn("MAX_MEMBER_SIZE = 512 * 1024 * 1024", helper)
        self.assertIn("MAX_OPERATION_SECONDS = 300", helper)
        for queue in ("send-queue", "job-queue", "manim-queue", "email-queue"):
            self.assertIn(f'"{queue}"', helper)
        transaction = (ROOT / "scripts/restore_transaction.py").read_text(
            encoding="utf-8"
        )
        sync = (ROOT / "sync.sh").read_text(encoding="utf-8")
        self.assertIn('"scripts/owner_state_lock.py"', sync)
        self.assertIn('"scripts/private_tmp.py"', sync)
        self.assertIn('"scripts/host_exec.py"', sync)
        self.assertIn('"scripts/file_delivery.py"', sync)
        self.assertIn('"scripts/restore_transaction.py"', sync)
        self.assertIn("RENAME_EXCHANGE", transaction)
        self.assertIn("--activate-reviewed-authority", restore)
        self.assertIn("ACTIVATE_REVIEWED_ARCHIVE_AUTHORITY", restore)
        self.assertIn("--validate-inherited", backup)
        self.assertIn("--validate-inherited", restore)
        self.assertIn("verify-extract", restore)
        self.assertLess(
            restore.index("verify-extract"),
            restore.rindex('"$SCRIPT_DIR/install.sh"'),
        )
        self.assertIn('  "devices"', backup)
        self.assertIn('"devices",', helper)
        self.assertIn('"identity",', helper)
        self.assertIn('"devices",', helper)
        self.assertIn("devices/**", manifest["private_archive"])
        self.assertIn("state/openclaw.sqlite", manifest["private_archive"])
        self.assertIn("agents/**/agent/openclaw-agent.sqlite", manifest["private_archive"])
        self.assertNotIn("completions/**", manifest["private_archive"])
        self.assertNotIn("cache/**", manifest["private_archive"])
        excluded = set(manifest["private_archive_exclude"])
        self.assertTrue(
            {
                "workspace/data/email-queue/**",
                "workspace/data/job-queue/**",
                "workspace/data/manim-queue/**",
                "workspace/data/send-queue/**",
                "workspace/.git/**",
                "workspace/scripts/**",
                "workspace/openclaw-scripts/**",
                "plugins/**",
                "extensions/**",
                "hooks/**",
                "skills/**",
            }.issubset(excluded)
        )
        self.assertNotIn('cp -a "$STAGE"/. "$PREFIX"/', restore)
        for mutation in (
            "systemctl --user stop",
            "systemctl --user start",
            "systemctl --user restart",
            "systemctl --user enable",
            "daemon-reload",
        ):
            self.assertNotIn(mutation, restore)
        self.assertNotIn("service_transaction.py", restore)
        self.assertNotIn("rollback_task.sh", restore)
        for executable_tree in (
            '"plugins"',
            '"extensions"',
            '"hooks"',
            '"skills"',
            '"scripts"',
            '"openclaw-scripts"',
        ):
            self.assertNotIn(executable_tree, helper.split("ROOT_TREES", 1)[1].split("WORKSPACE_ACTION_QUEUES", 1)[0])

    def test_owner_archive_rejects_action_queue_members(self) -> None:
        helper = ROOT / "scripts/owner_archive.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "queued.json"
            payload.write_text('{"action":"must-not-replay"}\n', encoding="utf-8")
            for queue in ("send-queue", "job-queue", "manim-queue", "email-queue"):
                with self.subTest(queue=queue):
                    archive = root / f"{queue}.tar.gz"
                    with tarfile.open(archive, "w:gz") as output:
                        output.add(payload, arcname=f"workspace/data/{queue}/queued.json")
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(helper),
                            "verify",
                            "--archive",
                            str(archive),
                            "--allow-legacy",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("outside the allowlist", result.stderr)

    def test_owner_archive_rejects_restored_code_and_executable_members(self) -> None:
        helper = ROOT / "scripts/owner_archive.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload.sh"
            payload.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            payload.chmod(0o700)
            for member, expected in (
                ("workspace/data/payload.sh", "executable member"),
                ("workspace/scripts/payload.sh", "outside the allowlist"),
                ("plugins/payload.sh", "outside the allowlist"),
                ("agents/main/agent/payload.py", "outside the allowlist"),
            ):
                with self.subTest(member=member):
                    archive = root / (member.replace("/", "-") + ".tar.gz")
                    with tarfile.open(archive, "w:gz") as output:
                        output.add(payload, arcname=member)
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(helper),
                            "verify",
                            "--archive",
                            str(archive),
                            "--allow-legacy",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(expected, result.stderr)

    def test_owner_archive_rejects_oversized_members_and_names_before_expansion(self) -> None:
        import gzip

        helper = ROOT / "scripts/owner_archive.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oversized = root / "oversized.tar.gz"
            information = tarfile.TarInfo("workspace/memory/huge.bin")
            information.size = 512 * 1024 * 1024 + 1
            with gzip.open(oversized, "wb") as stream:
                stream.write(information.tobuf(format=tarfile.GNU_FORMAT))
                stream.write(b"\0" * 1024)
            result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "verify",
                    "--archive",
                    str(oversized),
                    "--allow-legacy",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("per-file limit", result.stderr)

            payload = root / "payload"
            payload.write_text("inert\n", encoding="utf-8")
            long_name = root / "long-name.tar.gz"
            with tarfile.open(long_name, "w:gz") as archive:
                archive.add(
                    payload,
                    arcname="workspace/memory/" + "a" * 600,
                )
            result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "verify",
                    "--archive",
                    str(long_name),
                    "--allow-legacy",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("unsafe member path", result.stderr)

    def test_owner_archive_rejects_linked_inputs_and_destinations(self) -> None:
        helper = ROOT / "scripts/owner_archive.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.write_text("inert\n", encoding="utf-8")
            payload.chmod(0o600)
            archive = root / "legacy.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                output.add(payload, arcname="workspace/memory/inert.txt")
            archive.chmod(0o600)
            linked_archive = root / "linked.tar.gz"
            linked_archive.symlink_to(archive)
            verified = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "verify",
                    "--archive",
                    str(linked_archive),
                    "--allow-legacy",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(verified.returncode, 2)

            passphrase = root / "passphrase"
            passphrase.write_text("private\n", encoding="utf-8")
            passphrase.chmod(0o600)
            linked_passphrase = root / "linked-passphrase"
            linked_passphrase.symlink_to(passphrase)
            encrypted = root / "encrypted.gpg"
            encrypted_result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "encrypt",
                    "--source",
                    str(payload),
                    "--output",
                    str(encrypted),
                    "--passphrase-file",
                    str(linked_passphrase),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(encrypted_result.returncode, 2)
            self.assertFalse(encrypted.exists())

            victim = root / "victim"
            victim.write_text("preserve\n", encoding="utf-8")
            victim.chmod(0o600)
            linked_output = root / "linked-output.gpg"
            linked_output.symlink_to(victim)
            output_result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "encrypt",
                    "--source",
                    str(payload),
                    "--output",
                    str(linked_output),
                    "--passphrase-file",
                    str(passphrase),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(output_result.returncode, 2)
            self.assertTrue(linked_output.is_symlink())
            self.assertEqual(victim.read_text(), "preserve\n")

            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_gpg = fake_bin / "gpg"
            fake_canary = root / "attacker-gpg-ran"
            fake_gpg.write_text(
                f"#!/bin/sh\ntouch {fake_canary}\nexit 91\n",
                encoding="utf-8",
            )
            fake_gpg.chmod(0o700)
            hostile_gnupg = root / "gnupg"
            hostile_gnupg.mkdir(mode=0o700)
            hostile_home = root / "hostile-home"
            (hostile_home / ".gnupg").mkdir(parents=True, mode=0o700)
            status_canary = root / "hostile-status-output"
            logger_canary = root / "hostile-logger-output"
            plaintext_redirect = root / "hostile-plaintext-output"
            hostile_configuration = (
                f"status-file {status_canary}\n"
                f"logger-file {logger_canary}\n"
                f"output {plaintext_redirect}\n"
            )
            for configuration in (
                hostile_gnupg / "gpg.conf",
                hostile_home / ".gnupg/gpg.conf",
            ):
                configuration.write_text(hostile_configuration, encoding="utf-8")
                configuration.chmod(0o600)
            scoped_root = root / "owner-plaintext-tmp"
            scoped_root.mkdir(mode=0o700)
            bounded_output = root / "bounded.gpg"
            hostile_environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "HOME": str(hostile_home),
                "OPENAI_API_KEY": "must-not-reach-gpg",
                "GNUPGHOME": str(hostile_gnupg),
                "GPG_TTY": str(root / "hostile-tty"),
                "OPENCLAW_OWNER_PLAINTEXT_TMPDIR": str(scoped_root),
                "OPENCLAW_OWNER_PERSISTENT_PLAINTEXT_ACK": (
                    "ACKNOWLEDGE_PERSISTENT_OWNER_PLAINTEXT"
                ),
            }
            bounded_result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "encrypt",
                    "--source",
                    str(payload),
                    "--output",
                    str(bounded_output),
                    "--passphrase-file",
                    str(passphrase),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=hostile_environment,
            )
            self.assertEqual(bounded_result.returncode, 0, bounded_result.stderr)
            self.assertGreater(bounded_output.stat().st_size, 0)
            self.assertFalse(fake_canary.exists())
            self.assertFalse(status_canary.exists())
            self.assertFalse(logger_canary.exists())
            self.assertFalse(plaintext_redirect.exists())
            self.assertEqual(list(scoped_root.iterdir()), [])

            decrypted = root / "bounded-decrypted"
            decrypted_result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "decrypt",
                    "--source",
                    str(bounded_output),
                    "--output",
                    str(decrypted),
                    "--passphrase-file",
                    str(passphrase),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=hostile_environment,
            )
            self.assertEqual(decrypted_result.returncode, 0, decrypted_result.stderr)
            self.assertEqual(decrypted.read_text(encoding="utf-8"), "inert\n")
            self.assertFalse(status_canary.exists())
            self.assertFalse(logger_canary.exists())
            self.assertFalse(plaintext_redirect.exists())
            self.assertEqual(list(scoped_root.iterdir()), [])

            broken = root / "broken.gpg"
            broken.write_bytes(b"not-a-valid-gpg-payload")
            broken.chmod(0o600)
            failed_plaintext = root / "failed-plaintext-canary"
            failed_decrypt = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "decrypt",
                    "--source",
                    str(broken),
                    "--output",
                    str(failed_plaintext),
                    "--passphrase-file",
                    str(passphrase),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=hostile_environment,
            )
            self.assertEqual(failed_decrypt.returncode, 2)
            self.assertFalse(failed_plaintext.exists())
            self.assertNotIn("inert", failed_decrypt.stdout)
            self.assertNotIn("inert", failed_decrypt.stderr)
            self.assertEqual(list(scoped_root.iterdir()), [])

            override = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "encrypt",
                    "--source",
                    str(payload),
                    "--output",
                    str(root / "override.gpg"),
                    "--gpg",
                    str(fake_gpg),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(override.returncode, 2)
            self.assertIn("unrecognized arguments", override.stderr)
            self.assertFalse(fake_canary.exists())

    def test_owner_gpg_argv_environment_and_agent_cleanup_are_scoped(self) -> None:
        owner_archive = load_runtime_module(
            "owner_archive_gpg_invocation_test", "scripts/owner_archive.py"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            scoped_root = root / "scoped-root"
            scoped_root.mkdir(mode=0o700)
            source = root / "source"
            source.write_bytes(b"offline-plaintext-canary")
            source.chmod(0o600)
            passphrase = root / "passphrase"
            passphrase.write_text("offline-passphrase\n", encoding="utf-8")
            passphrase.chmod(0o600)
            destination = root / "destination.gpg"
            calls: list[tuple[list[str], dict[str, object]]] = []

            def fake_run(
                arguments: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                calls.append((list(arguments), dict(kwargs)))
                if arguments[0] == "/usr/bin/gpg":
                    output_index = arguments.index("--output") + 1
                    output_value = arguments[output_index]
                    self.assertRegex(output_value, r"^/proc/self/fd/[0-9]+$")
                    output_descriptor = int(output_value.rsplit("/", 1)[1])
                    os.write(output_descriptor, b"offline-ciphertext")
                return subprocess.CompletedProcess(arguments, 0, b"", b"")

            hostile_environment = {
                "HOME": str(root / "hostile-home"),
                "GNUPGHOME": str(root / "hostile-gnupg"),
                "PATH": str(root / "hostile-bin"),
                "OPENAI_API_KEY": "ambient-secret-canary",
                "GPG_TTY": str(root / "hostile-tty"),
            }
            with mock.patch.dict(os.environ, hostile_environment, clear=False), mock.patch.object(
                owner_archive,
                "_owner_plaintext_temp_root",
                return_value=scoped_root,
            ), mock.patch.object(
                owner_archive.subprocess,
                "run",
                side_effect=fake_run,
            ):
                owner_archive.crypt_owner_file(
                    decrypt=False,
                    source=source,
                    destination=destination,
                    passphrase_file=passphrase,
                )

            self.assertEqual(destination.read_bytes(), b"offline-ciphertext")
            self.assertEqual(len(calls), 2)
            gpg_arguments, gpg_options = calls[0]
            self.assertEqual(gpg_arguments[0], "/usr/bin/gpg")
            for option in (
                "--no-options",
                "--homedir",
                "--batch",
                "--no-tty",
                "--yes",
                "--no-symkey-cache",
                "--pinentry-mode",
                "--passphrase-file",
                "--output",
                "--compress-algo",
                "--symmetric",
                "--cipher-algo",
            ):
                self.assertIn(option, gpg_arguments)
            self.assertNotIn("-", gpg_arguments[gpg_arguments.index("--output") + 1])
            scoped_home = Path(gpg_arguments[gpg_arguments.index("--homedir") + 1])
            self.assertEqual(scoped_home.parent, scoped_root)
            self.assertRegex(scoped_home.name, r"^\.openclaw-gpg-[0-9a-f]{32}$")
            self.assertEqual(
                gpg_options["env"],
                {
                    "GNUPGHOME": str(scoped_home),
                    "HOME": "/",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": str(scoped_home),
                },
            )
            self.assertEqual(gpg_options["stdout"], subprocess.DEVNULL)
            self.assertTrue(str(gpg_options["executable"]).startswith("/proc/self/fd/"))
            output_descriptor = int(
                gpg_arguments[gpg_arguments.index("--output") + 1].rsplit("/", 1)[1]
            )
            self.assertIn(output_descriptor, gpg_options["pass_fds"])

            cleanup_arguments, cleanup_options = calls[1]
            self.assertEqual(
                cleanup_arguments,
                [
                    "/usr/bin/gpgconf",
                    "--homedir",
                    str(scoped_home),
                    "--kill",
                    "gpg-agent",
                ],
            )
            self.assertEqual(cleanup_options["env"], gpg_options["env"])
            self.assertEqual(cleanup_options["stdout"], subprocess.DEVNULL)
            self.assertEqual(cleanup_options["stderr"], subprocess.DEVNULL)
            self.assertEqual(list(scoped_root.iterdir()), [])

            failed_destination = root / "cleanup-failed.gpg"
            with mock.patch.object(
                owner_archive,
                "_owner_plaintext_temp_root",
                return_value=scoped_root,
            ), mock.patch.object(
                owner_archive.subprocess,
                "run",
                side_effect=fake_run,
            ), mock.patch.object(
                owner_archive,
                "_terminate_scoped_gpg_agent",
                side_effect=owner_archive.ArchiveError(
                    "could not terminate the scoped GPG agent"
                ),
            ):
                with self.assertRaisesRegex(
                    owner_archive.ArchiveError,
                    "could not terminate the scoped GPG agent",
                ):
                    owner_archive.crypt_owner_file(
                        decrypt=False,
                        source=source,
                        destination=failed_destination,
                        passphrase_file=passphrase,
                    )
            self.assertFalse(failed_destination.exists())
            self.assertEqual(list(scoped_root.iterdir()), [])

    def test_restore_requires_explicit_isolated_legacy_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "pending.json"
            payload.write_text("{}\n", encoding="utf-8")
            plaintext = root / "legacy.tar.gz"
            archive = root / "legacy.tar.gz.gpg"
            with tarfile.open(plaintext, "w:gz") as output:
                output.add(payload, arcname="devices/pending.json")
                output.add(payload, arcname="workspace/memory/legacy.json")
            crypto_environment = encrypt_restore_fixture(root, plaintext, archive)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_gpg = fake_bin / "gpg"
            fake_gpg_canary = root / "path-gpg-ran"
            fake_gpg.write_text(
                f"#!/bin/sh\ntouch {fake_gpg_canary}\nexit 91\n",
                encoding="utf-8",
            )
            fake_gpg.chmod(0o700)
            destination = root / "destination"
            base = [
                str(ROOT / "restore.sh"),
                "--archive",
                str(archive),
                "--prefix",
                str(destination),
                "--skip-services",
            ]
            environment = {
                **os.environ,
                **crypto_environment,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            }
            default = subprocess.run(
                [*base, "--overlay-only"],
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
            self.assertEqual(default.returncode, 2)
            self.assertFalse(destination.exists())
            unisolated = subprocess.run(
                [*base, "--allow-legacy"],
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
            self.assertEqual(unisolated.returncode, 2)
            self.assertFalse(destination.exists())
            accepted = subprocess.run(
                [*base, "--overlay-only", "--allow-legacy"],
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertFalse(fake_gpg_canary.exists())
            self.assertFalse((destination / "devices/pending.json").exists())
            self.assertEqual(
                (destination / "workspace/memory/legacy.json").read_text(), "{}\n"
            )

    def test_owner_state_lock_serializes_and_rejects_a_fabricated_marker(self) -> None:
        helper = ROOT / "scripts/owner_state_lock.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / ".state.owner-state.lock"
            fabricated_environment = {
                **os.environ,
                "OPENCLAW_OWNER_STATE_LOCK": str(lock),
                "OPENCLAW_OWNER_STATE_LOCK_FD": "999999",
            }
            fabricated = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "--lock-path",
                    str(lock),
                    "--validate-inherited",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env=fabricated_environment,
            )
            self.assertEqual(fabricated.returncode, 2)

            holder = subprocess.Popen(
                [
                    sys.executable,
                    str(helper),
                    "--lock-path",
                    str(lock),
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; print('ready', flush=True); sys.stdin.readline()",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert holder.stdout is not None
                self.assertEqual(holder.stdout.readline().strip(), "ready")
                contender = subprocess.run(
                    [
                        sys.executable,
                        str(helper),
                        "--lock-path",
                        str(lock),
                        "--",
                        "/bin/true",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(contender.returncode, 2)
                self.assertIn("already held", contender.stderr)
            finally:
                if holder.stdin is not None:
                    holder.stdin.write("\n")
                    holder.stdin.flush()
                holder.communicate(timeout=10)

    def test_alternate_restore_prefix_still_checks_the_selected_active_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.write_text("inert\n", encoding="utf-8")
            archive = root / "legacy.tar.gz.gpg"
            with tarfile.open(archive, "w:gz") as output:
                output.add(payload, arcname="workspace/memory/inert.txt")
            archive.chmod(0o600)
            destination = root / "alternate-state"
            writer = subprocess.Popen(
                [
                    "/usr/bin/bash",
                    "-c",
                    "exec -a /openclaw/dist/index.js-test-gateway /usr/bin/sleep 30",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                result = subprocess.run(
                    [
                        str(ROOT / "restore.sh"),
                        "--archive",
                        str(archive),
                        "--prefix",
                        str(destination),
                        "--overlay-only",
                        "--allow-legacy",
                        "--skip-services",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env={
                        **os.environ,
                        "OPENCLAW_ACTIVE_RUNTIME_HOME": str(destination),
                    },
                )
            finally:
                writer.terminate()
                writer.wait(timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("writer is active", result.stderr)
            self.assertFalse(destination.exists())

    @unittest.skipUnless(
        Path("/usr/bin/gpg").is_file()
        and (Path.home() / ".npm-global/lib/node_modules/openclaw/openclaw.mjs").is_file(),
        "gpg and OpenClaw are required for owner archive roundtrip",
    )
    def test_owner_backup_roundtrips_device_pairing_state_privately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            devices = source / "devices"
            devices.mkdir(parents=True, mode=0o700)
            devices.chmod(0o700)
            paired = devices / "paired.json"
            pending = devices / "pending.json"
            paired.write_text(
                '{"device-fixture":{"tokens":{"operator":"pairing-canary"}}}\n',
                encoding="utf-8",
            )
            pending.write_text("{}\n", encoding="utf-8")
            paired.chmod(0o600)
            pending.chmod(0o600)
            delivery_policy = source / "file-delivery-policy.json"
            delivery_policy.write_text(
                json.dumps(
                    {
                        "schema": "openclaw.file-delivery-policy/v1",
                        "delivery_policy": {
                            "allowed_targets": {
                                "telegram": ["archive-approved-chat"],
                                "zulip": [],
                                "googlechat": [],
                                "whatsapp": [],
                                "zalo": [],
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            delivery_policy.chmod(0o600)
            (source / "completions").mkdir()
            (source / "completions/openclaw.bash").write_text(
                "completion-cache-canary\n", encoding="utf-8"
            )
            (source / "cache").mkdir()
            (source / "cache/generated").write_text(
                "generated-cache-canary\n", encoding="utf-8"
            )
            agent_dir = source / "agents/main/agent"
            agent_dir.mkdir(parents=True)
            agent_db = agent_dir / "openclaw-agent.sqlite"
            connection = sqlite3.connect(agent_db)
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA user_version = 1;
                CREATE TABLE schema_meta (
                  meta_key TEXT PRIMARY KEY, role TEXT NOT NULL,
                  schema_version INTEGER NOT NULL, agent_id TEXT,
                  app_version TEXT, created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                CREATE TABLE auth_profile_store (
                  store_key TEXT PRIMARY KEY, store_json TEXT NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                CREATE TABLE auth_profile_state (
                  state_key TEXT PRIMARY KEY, state_json TEXT NOT NULL,
                  updated_at INTEGER NOT NULL
                );
                INSERT INTO schema_meta VALUES
                  ('primary', 'agent', 1, 'main', NULL, 1, 1);
                INSERT INTO auth_profile_store VALUES
                  ('primary', '{"version":1,"profiles":{}}', 1);
                """
            )
            connection.commit()
            agent_db.chmod(0o600)
            models_secret_canary = "owner-models-secret-canary-never-print"
            models_json = agent_dir / "models.json"
            models_json.write_text(
                json.dumps(
                    {
                        "providers": {
                            "fixture": {"apiKey": models_secret_canary},
                            "placeholder": {"apiKey": "{{ REDACTED }}"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            models_json.chmod(0o600)
            connection.close()
            global_secret_canary = "global-state-secret-canary-never-print"
            device_private_key_canary = (
                "device-private-key-canary-never-print"
            )
            vapid_private_key_canary = "vapid-private-key-canary-never-print"
            global_state_dir = source / "state"
            global_state_dir.mkdir(mode=0o700)
            global_db = global_state_dir / "openclaw.sqlite"
            with sqlite3.connect(global_db) as global_connection:
                global_connection.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA user_version = 1;
                    CREATE TABLE schema_meta (
                      meta_key TEXT PRIMARY KEY, role TEXT NOT NULL,
                      schema_version INTEGER NOT NULL, agent_id TEXT,
                      app_version TEXT, created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE TABLE auth_profile_stores (
                      store_key TEXT PRIMARY KEY, store_json TEXT NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE TABLE auth_profile_state (
                      store_key TEXT PRIMARY KEY, state_json TEXT NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE TABLE device_identities (
                      identity_key TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                      public_key_pem TEXT NOT NULL, private_key_pem TEXT NOT NULL,
                      created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL
                    );
                    CREATE TABLE device_auth_tokens (
                      device_id TEXT NOT NULL, role TEXT NOT NULL, token TEXT NOT NULL,
                      scopes_json TEXT NOT NULL, updated_at_ms INTEGER NOT NULL,
                      PRIMARY KEY (device_id, role)
                    );
                    CREATE TABLE device_bootstrap_tokens (
                      token_key TEXT PRIMARY KEY, token TEXT NOT NULL, ts INTEGER,
                      device_id TEXT, public_key TEXT, profile_json TEXT,
                      redeemed_profile_json TEXT, pending_profile_json TEXT,
                      issued_at_ms INTEGER, last_used_at_ms INTEGER
                    );
                    CREATE TABLE web_push_subscriptions (
                      endpoint_hash TEXT PRIMARY KEY, subscription_id TEXT NOT NULL,
                      endpoint TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                      created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL
                    );
                    CREATE TABLE web_push_vapid_keys (
                      key_id TEXT PRIMARY KEY, public_key TEXT NOT NULL,
                      private_key TEXT NOT NULL, subject TEXT NOT NULL,
                      updated_at_ms INTEGER NOT NULL
                    );
                    CREATE TABLE apns_registrations (
                      node_id TEXT PRIMARY KEY, transport TEXT NOT NULL, token TEXT,
                      relay_handle TEXT, send_grant TEXT, installation_id TEXT,
                      topic TEXT NOT NULL, environment TEXT NOT NULL,
                      distribution TEXT, token_debug_suffix TEXT,
                      updated_at_ms INTEGER NOT NULL
                    );
                    INSERT INTO schema_meta VALUES
                      ('primary', 'global', 1, NULL, NULL, 1, 1);
                    """
                )
                global_connection.execute(
                    "INSERT INTO auth_profile_stores VALUES ('primary', ?, 1)",
                    (json.dumps({"credential": global_secret_canary}),),
                )
                global_connection.execute(
                    "INSERT INTO device_identities VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "primary",
                        "device-fixture",
                        "public-device-fixture",
                        device_private_key_canary,
                        1,
                        1,
                    ),
                )
                global_connection.execute(
                    "INSERT INTO web_push_vapid_keys VALUES (?, ?, ?, ?, ?)",
                    (
                        "primary",
                        "public-vapid-fixture",
                        vapid_private_key_canary,
                        "mailto:owner@example.invalid",
                        1,
                    ),
                )
            global_db.chmod(0o600)
            calibre_dir = source / "workspace/data/calibre-library"
            calibre_dir.mkdir(parents=True)
            calibre_db = calibre_dir / "metadata.db"
            calibre_connection = sqlite3.connect(calibre_db)
            calibre_connection.execute("PRAGMA journal_mode = WAL")
            calibre_connection.execute(
                "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
            )
            calibre_connection.execute(
                "INSERT INTO books (title) VALUES ('transactional snapshot fixture')"
            )
            calibre_connection.commit()
            calibre_db.chmod(0o600)

            passphrase = root / "passphrase"
            passphrase.write_text("owner-archive-test-passphrase\n", encoding="utf-8")
            passphrase.chmod(0o600)
            gnupg = root / "gnupg"
            gnupg.mkdir(mode=0o700)
            output = root / "backups"
            environment = {
                **os.environ,
                "GNUPGHOME": str(gnupg),
                "OPENCLAW_BACKUP_PASSPHRASE_FILE": str(passphrase),
            }
            passphrase.chmod(0o644)
            unsafe_backup = subprocess.run(
                [
                    str(ROOT / "backup.sh"),
                    "--prefix",
                    str(source),
                    "--output",
                    str(output),
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            self.assertNotEqual(unsafe_backup.returncode, 0)
            self.assertEqual(list(output.glob("openclaw-private-*.tar.gz.gpg")), [])
            passphrase.chmod(0o600)
            backup_result = subprocess.run(
                [
                    str(ROOT / "backup.sh"),
                    "--prefix",
                    str(source),
                    "--output",
                    str(output),
                    "--verify",
                ],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotIn(
                models_secret_canary,
                backup_result.stdout + backup_result.stderr,
            )
            self.assertNotIn(
                global_secret_canary,
                backup_result.stdout + backup_result.stderr,
            )
            self.assertNotIn(
                device_private_key_canary,
                backup_result.stdout + backup_result.stderr,
            )
            self.assertNotIn(
                vapid_private_key_canary,
                backup_result.stdout + backup_result.stderr,
            )
            calibre_connection.close()
            archives = list(output.glob("openclaw-private-*.tar.gz.gpg"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(list(output.glob(".openclaw-owner-publish.*")), [])

            destination = root / "destination"
            passphrase.chmod(0o644)
            unsafe_restore = subprocess.run(
                [
                    str(ROOT / "restore.sh"),
                    "--archive",
                    str(archives[0]),
                    "--prefix",
                    str(destination),
                    "--overlay-only",
                    "--skip-services",
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            self.assertNotEqual(unsafe_restore.returncode, 0)
            self.assertFalse(destination.exists())
            passphrase.chmod(0o600)
            restore_result = subprocess.run(
                [
                    str(ROOT / "restore.sh"),
                    "--archive",
                    str(archives[0]),
                    "--prefix",
                    str(destination),
                    "--overlay-only",
                    "--skip-services",
                ],
                check=True,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            for canary in (
                global_secret_canary,
                device_private_key_canary,
                vapid_private_key_canary,
            ):
                self.assertNotIn(
                    canary, restore_result.stdout + restore_result.stderr
                )

            self.assertFalse((destination / "devices").exists())
            restored_devices = (
                destination
                / "recovery-quarantine/archive-authority/payload/devices"
            )
            self.assertEqual(restored_devices.stat().st_mode & 0o777, 0o700)
            for original in (paired, pending):
                restored = restored_devices / original.name
                self.assertEqual(restored.read_bytes(), original.read_bytes())
                self.assertEqual(restored.stat().st_mode & 0o777, 0o600)
            self.assertFalse((destination / delivery_policy.name).exists())
            quarantined_policy = (
                destination
                / "recovery-quarantine/archive-authority/payload"
                / delivery_policy.name
            )
            self.assertEqual(
                quarantined_policy.read_bytes(), delivery_policy.read_bytes()
            )
            self.assertEqual(quarantined_policy.stat().st_mode & 0o777, 0o600)
            self.assertFalse((destination / "state").exists())
            restored_global = (
                destination
                / "recovery-quarantine/archive-authority/payload/state/openclaw.sqlite"
            )
            self.assertTrue(restored_global.is_file())
            self.assertEqual(restored_global.stat().st_mode & 0o777, 0o600)
            self.assertFalse(restored_global.with_name("openclaw.sqlite-wal").exists())
            with sqlite3.connect(restored_global) as restored_connection:
                self.assertEqual(
                    restored_connection.execute("PRAGMA quick_check").fetchone(),
                    ("ok",),
                )
                restored_global_store = json.loads(
                    restored_connection.execute(
                        "SELECT store_json FROM auth_profile_stores "
                        "WHERE store_key='primary'"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    restored_global_store["credential"], global_secret_canary
                )
                self.assertEqual(
                    restored_connection.execute(
                        "SELECT private_key_pem FROM device_identities "
                        "WHERE identity_key='primary'"
                    ).fetchone(),
                    (device_private_key_canary,),
                )
                self.assertEqual(
                    restored_connection.execute(
                        "SELECT private_key FROM web_push_vapid_keys "
                        "WHERE key_id='primary'"
                    ).fetchone(),
                    (vapid_private_key_canary,),
                )
            self.assertFalse((destination / "completions").exists())
            self.assertFalse((destination / "cache").exists())
            restored_db = destination / "agents/main/agent/openclaw-agent.sqlite"
            self.assertTrue(restored_db.is_file())
            self.assertEqual(restored_db.stat().st_mode & 0o777, 0o600)
            self.assertFalse((destination / "agents/main/agent/openclaw-agent.sqlite-wal").exists())
            with sqlite3.connect(restored_db) as restored_connection:
                self.assertEqual(restored_connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
                self.assertEqual(
                    restored_connection.execute(
                        "SELECT count(*) FROM auth_profile_store"
                    ).fetchone(),
                    (1,),
                )
                restored_store = json.loads(
                    restored_connection.execute(
                        "SELECT store_json FROM auth_profile_store "
                        "WHERE store_key='primary'"
                    ).fetchone()[0]
                )
                self.assertEqual(restored_store["profiles"], {})
            restored_calibre = (
                destination / "workspace/data/calibre-library/metadata.db"
            )
            self.assertTrue(restored_calibre.is_file())
            self.assertFalse(restored_calibre.with_name("metadata.db-wal").exists())
            with sqlite3.connect(restored_calibre) as restored_connection:
                self.assertEqual(
                    restored_connection.execute("PRAGMA quick_check").fetchone(),
                    ("ok",),
                )
                self.assertEqual(
                    restored_connection.execute("SELECT count(*) FROM books").fetchone(),
                    (1,),
                )

            reviewed_destination = root / "reviewed-destination"
            reviewed_restore = subprocess.run(
                [
                    str(ROOT / "restore.sh"),
                    "--archive",
                    str(archives[0]),
                    "--prefix",
                    str(reviewed_destination),
                    "--overlay-only",
                    "--skip-services",
                    "--activate-reviewed-authority",
                    "ACTIVATE_REVIEWED_ARCHIVE_AUTHORITY",
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(reviewed_restore.returncode, 0, reviewed_restore.stderr)
            for canary in (
                global_secret_canary,
                device_private_key_canary,
                vapid_private_key_canary,
            ):
                self.assertNotIn(
                    canary, reviewed_restore.stdout + reviewed_restore.stderr
                )
            self.assertEqual(
                (reviewed_destination / "devices/paired.json").read_bytes(),
                paired.read_bytes(),
            )
            self.assertEqual(
                (reviewed_destination / delivery_policy.name).read_bytes(),
                delivery_policy.read_bytes(),
            )
            with sqlite3.connect(
                reviewed_destination / "agents/main/agent/openclaw-agent.sqlite"
            ) as reviewed_connection:
                reviewed_store = json.loads(
                    reviewed_connection.execute(
                        "SELECT store_json FROM auth_profile_store "
                        "WHERE store_key='primary'"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    reviewed_store["profiles"]["fixture:default"]["key"],
                    models_secret_canary,
                )
            reviewed_global = reviewed_destination / "state/openclaw.sqlite"
            self.assertTrue(reviewed_global.is_file())
            with sqlite3.connect(reviewed_global) as restored_connection:
                self.assertEqual(
                    restored_connection.execute(
                        "SELECT role, agent_id, schema_version FROM schema_meta "
                        "WHERE meta_key='primary'"
                    ).fetchone(),
                    ("global", None, 1),
                )
                self.assertEqual(
                    restored_connection.execute(
                        "SELECT private_key_pem FROM device_identities "
                        "WHERE identity_key='primary'"
                    ).fetchone(),
                    (device_private_key_canary,),
                )
                self.assertEqual(
                    restored_connection.execute(
                        "SELECT private_key FROM web_push_vapid_keys "
                        "WHERE key_id='primary'"
                    ).fetchone(),
                    (vapid_private_key_canary,),
                )

    def test_public_component_never_installs_redacted_agent_credentials(self) -> None:
        manifest = json.loads(
            (ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8")
        )
        agent_entries = [
            item
            for item in manifest["classifications"]
            if item.get("source") == "agents" or item.get("dest") == "agents"
        ]
        self.assertEqual(agent_entries, [])
        self.assertEqual(list((ROOT / "agents").glob("*/agent/auth-profiles.json")), [])
        self.assertEqual(list((ROOT / "agents").glob("*/agent/models.json")), [])
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn('render_copy "$SCRIPT_DIR/agents"', installer)
        self.assertIn("quarantine-placeholders", installer)

    def test_upgrade_quarantines_placeholder_auth_in_all_agent_directories(self) -> None:
        helper = ROOT / "scripts/openclaw_auth_closure.py"
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / ".openclaw"
            main = prefix / "agents/main/agent"
            orphan = prefix / "agents/orphan/agent"
            main.mkdir(parents=True)
            orphan.mkdir(parents=True)
            prefix.chmod(0o700)
            sentinel_value = "redaction-sentinel-canary"
            placeholder = main / "auth-profiles.json"
            placeholder.write_text(
                '{"profiles":{"fixture":{"key":"{{ REDACTED }}"}}}\n',
                encoding="utf-8",
            )
            placeholder.chmod(0o600)
            model_placeholder = orphan / "models.json"
            model_placeholder.write_text(
                '{"providers":{"fixture":{"apiKey":"{{ SECRET_VALUE }}"}}}\n',
                encoding="utf-8",
            )
            model_placeholder.chmod(0o600)
            legitimate = orphan / "auth.json"
            legitimate.write_text(
                f'{{"fixture":"{sentinel_value}"}}\n', encoding="utf-8"
            )
            legitimate.chmod(0o600)

            result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "quarantine-placeholders",
                    "--prefix",
                    str(prefix),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # Installer cleanup is deliberately placeholder-only: legitimate
            # legacy credentials remain available for the lossless DB
            # materialization step.
            self.assertEqual(json.loads(result.stdout)["fileCount"], 2)
            self.assertFalse(placeholder.exists())
            self.assertFalse(model_placeholder.exists())
            self.assertTrue(legitimate.exists())
            quarantined = list(
                (prefix / "recovery-quarantine/legacy-agent-authority").glob("*/*/*")
            )
            self.assertEqual(len(quarantined), 2)
            self.assertNotIn("REDACTED", result.stdout + result.stderr)

    def test_auth_migration_delegates_service_lifecycle_to_restore_owner(self) -> None:
        helper = (ROOT / "scripts/openclaw_auth_closure.py").read_text(
            encoding="utf-8"
        )
        restore = (ROOT / "restore.sh").read_text(encoding="utf-8")
        self.assertIn("offline-structural-only", helper)
        self.assertIn('"openclawExecuted": False', helper)
        self.assertNotIn("subprocess", helper)
        self.assertNotIn("systemctl", helper)
        for mutation in (
            "systemctl --user stop",
            "systemctl --user start",
            "systemctl --user restart",
            "systemctl --user enable",
            "daemon-reload",
        ):
            self.assertNotIn(mutation, restore)
        self.assertNotIn("service_transaction.py", restore)

    def test_backup_never_materializes_or_mutates_live_agent_auth(self) -> None:
        backup = (ROOT / "backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("materialize-legacy", backup)
        self.assertNotIn("AUTH_CLOSURE_HELPER", backup)
        self.assertIn("Backup is intentionally non-mutating", backup)

    def test_auth_closure_gate_accepts_canonical_store_and_rejects_models_json(self) -> None:
        helper = ROOT / "scripts/openclaw_auth_closure.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            prefix = home / ".openclaw"
            agent_dir = prefix / "agents/main/agent"
            agent_dir.mkdir(parents=True)
            (prefix / "openclaw.json").write_text("{}\n", encoding="utf-8")
            database = agent_dir / "openclaw-agent.sqlite"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    PRAGMA user_version = 1;
                    CREATE TABLE schema_meta (
                      meta_key TEXT PRIMARY KEY, role TEXT NOT NULL,
                      schema_version INTEGER NOT NULL, agent_id TEXT,
                      app_version TEXT, created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE TABLE auth_profile_store (
                      store_key TEXT PRIMARY KEY, store_json TEXT NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    CREATE TABLE auth_profile_state (
                      state_key TEXT PRIMARY KEY, state_json TEXT NOT NULL,
                      updated_at INTEGER NOT NULL
                    );
                    INSERT INTO schema_meta VALUES
                      ('primary', 'agent', 1, 'main', NULL, 1, 1);
                    """
                )
                connection.execute(
                    "INSERT INTO auth_profile_store VALUES ('primary', ?, 1)",
                    (
                        json.dumps(
                            {
                                "version": 1,
                                "profiles": {
                                    "fixture:default": {
                                        "type": "api_key",
                                        "provider": "fixture",
                                        "key": "offline-fixture",
                                    }
                                },
                            }
                        ),
                    ),
                )
            database.chmod(0o600)
            fake = root / "openclaw"
            canary = root / "openclaw-executed"
            fake.write_text(
                f"#!/bin/sh\ntouch {canary}\nexit 99\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            argv = [
                sys.executable,
                str(helper),
                "verify",
                "--prefix",
                str(prefix),
                "--home",
                str(home),
                "--openclaw",
                str(fake),
                "--expected-version",
                "2026.7.1-2",
            ]
            passed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            passed_report = json.loads(passed.stdout)
            self.assertEqual(passed_report["status"], "PASS")
            self.assertEqual(passed_report["verificationMode"], "offline-structural-only")
            self.assertFalse(passed_report["openclawExecuted"])
            self.assertFalse(passed_report["networkEnabled"])
            self.assertFalse(canary.exists())
            store_metadata = passed_report["agents"][0]["canonicalStore"]
            information = database.stat()
            self.assertEqual(
                {
                    key: store_metadata[key]
                    for key in ("device", "inode", "size", "mtimeNs", "ctimeNs")
                },
                {
                    "device": information.st_dev,
                    "inode": information.st_ino,
                    "size": information.st_size,
                    "mtimeNs": information.st_mtime_ns,
                    "ctimeNs": information.st_ctime_ns,
                },
            )

            executable_ref = {
                "profiles": {
                    "fixture": {
                        "type": "api_key",
                        "provider": "fixture",
                        "keyRef": {
                            "source": "exec",
                            "provider": "host-command",
                            "id": "credential",
                        },
                    }
                }
            }
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE auth_profile_store SET store_json = ?",
                    (json.dumps(executable_ref),),
                )
            executable = subprocess.run(
                argv, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(executable.returncode, 2, executable.stderr)
            executable_report = json.loads(executable.stdout)
            self.assertIn(
                "canonical-store-executable-secret-ref",
                executable_report["agents"][0]["reasons"],
            )
            self.assertFalse(canary.exists())
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE auth_profile_store SET store_json = ?",
                    (
                        json.dumps(
                            {
                                "version": 1,
                                "profiles": {
                                    "fixture:default": {
                                        "type": "api_key",
                                        "provider": "fixture",
                                        "key": "offline-fixture",
                                    }
                                },
                            }
                        ),
                    ),
                )

            legacy = agent_dir / "MODELS.JSON.backup-2025"
            legacy.write_text('{"provider":"inert"}\n', encoding="utf-8")
            legacy.chmod(0o600)
            legacy_result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "OPENAI_API_KEY": "must-not-project"},
            )
            self.assertEqual(legacy_result.returncode, 2, legacy_result.stderr)
            self.assertEqual(
                json.loads(legacy_result.stdout)["failures"][0]["reason"],
                "legacy-agent-authority-remains",
            )
            self.assertFalse(canary.exists())
            legacy.unlink()

            nested_sentinel = json.dumps(
                {"outer": json.dumps({"value": "\\u007b\\u007b REDACTED \\u007d\\u007d"})}
            )
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE auth_profile_store SET store_json = ?",
                    (nested_sentinel,),
                )
            sentinel = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            self.assertEqual(sentinel.returncode, 2, sentinel.stderr)
            sentinel_report = json.loads(sentinel.stdout)
            self.assertIn(
                "canonical-store-redaction-sentinel",
                sentinel_report["agents"][0]["reasons"],
            )
            self.assertNotIn("REDACTED", sentinel.stdout + sentinel.stderr)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE auth_profile_store SET store_json = ?",
                    (
                        json.dumps(
                            {
                                "version": 1,
                                "profiles": {
                                    "fixture:default": {
                                        "type": "api_key",
                                        "provider": "fixture",
                                        "key": "offline-fixture",
                                    }
                                },
                            }
                        ),
                    ),
                )
                connection.execute(
                    "UPDATE auth_profile_store SET store_json = ?",
                    (json.dumps({"value": "｛｛ ＲＥＤＡＣＴＥＤ ｝｝"}),),
                )
            normalized = subprocess.run(
                argv, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(normalized.returncode, 2, normalized.stderr)
            self.assertIn(
                "canonical-store-redaction-sentinel",
                json.loads(normalized.stdout)["agents"][0]["reasons"],
            )
            self.assertFalse(canary.exists())

    def test_auth_closure_quarantines_reoverlaid_legacy_profiles(self) -> None:
        helper = ROOT / "scripts/openclaw_auth_closure.py"
        expected_version = json.loads(
            (ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8")
        )["openclaw"]["observed_version"]
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            prefix = home / ".openclaw"
            agent_dir = prefix / "agents/main/agent"
            agent_dir.mkdir(parents=True)
            prefix.chmod(0o700)
            (prefix / "openclaw.json").write_text(
                """{
  "gateway": {"mode": "local"},
  "agents": {"defaults": {"model": {"primary": "openai/gpt-5.5"}}}
}
""",
                encoding="utf-8",
            )
            legacy_payload = """{
  "version": 1,
  "profiles": {
    "openai:default": {
      "type": "api_key",
      "provider": "openai",
      "key": "offline-fixture"
    }
  }
}
"""
            legacy = agent_dir / "auth-profiles.json"
            argv = [
                sys.executable,
                str(helper),
                "migrate",
                "--prefix",
                str(prefix),
                "--home",
                str(home),
                "--expected-version",
                expected_version,
                "--allow-unconfigured",
            ]
            for name in ("auth-profiles.json", "AUTH-PROFILES.JSON.backup-2025"):
                legacy = agent_dir / name
                legacy.write_text(legacy_payload, encoding="utf-8")
                legacy.chmod(0o600)
                result = subprocess.run(
                    argv, capture_output=True, text=True, timeout=60
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], "PASS")
                self.assertFalse(legacy.exists())
                report = json.loads(result.stdout)
                self.assertFalse(report["openclawExecuted"])
                self.assertFalse(report["networkEnabled"])
                self.assertEqual(
                    report["legacyAuthorityMaterialization"]["status"], "PASS"
                )
            quarantine_roots = list(
                (prefix / "recovery-quarantine/legacy-agent-authority").glob("*")
            )
            self.assertEqual(len(quarantine_roots), 2)
            self.assertTrue(
                all((root / "QUARANTINE.json").is_file() for root in quarantine_roots)
            )
            database = agent_dir / "openclaw-agent.sqlite"
            with sqlite3.connect(database) as connection:
                store = json.loads(
                    connection.execute(
                        "SELECT store_json FROM auth_profile_store WHERE store_key='primary'"
                    ).fetchone()[0]
                )
            self.assertEqual(
                store["profiles"]["openai:default"]["key"], "offline-fixture"
            )

    def test_markerless_owner_archive_rejects_direct_sqlite_capture(self) -> None:
        helper = ROOT / "scripts/owner_archive.py"
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "legacy.tar.gz"
            payload = Path(temporary) / "openclaw-agent.sqlite"
            payload.write_bytes(b"not-a-snapshot")
            with tarfile.open(archive, "w:gz") as output:
                output.add(
                    payload,
                    arcname="agents/main/agent/openclaw-agent.sqlite",
                )
            result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "verify",
                    "--archive",
                    str(archive),
                    "--allow-legacy",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 2)
            self.assertTrue(
                "unsnapshotted SQLite" in result.stderr
                or "SQLite snapshot is unreadable" in result.stderr
            )

    def test_standalone_restore_authenticates_archive_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.write_text("not owner data\n", encoding="utf-8")
            plaintext = root / "invalid-owner.tar.gz"
            archive = root / "invalid-owner.tar.gz.gpg"
            with tarfile.open(plaintext, "w:gz") as output:
                output.add(payload, arcname="outside-owner-allowlist")
            crypto_environment = encrypt_restore_fixture(root, plaintext, archive)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_gpg = fake_bin / "gpg"
            fake_gpg_canary = root / "path-gpg-ran"
            fake_gpg.write_text(
                f"#!/bin/sh\ntouch {fake_gpg_canary}\nexit 91\n",
                encoding="utf-8",
            )
            fake_gpg.chmod(0o700)
            destination = root / "destination"
            result = subprocess.run(
                [
                    str(ROOT / "restore.sh"),
                    "--archive",
                    str(archive),
                    "--prefix",
                    str(destination),
                    "--skip-services",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    **crypto_environment,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())
            self.assertFalse(fake_gpg_canary.exists())

    def test_overlay_restore_preserves_authorities_and_quarantines_queues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_root = root / "archive"
            (archive_root / "devices").mkdir(parents=True)
            (archive_root / "cron").mkdir(parents=True)
            (archive_root / "tasks").mkdir(parents=True)
            (archive_root / "flows").mkdir(parents=True)
            (archive_root / "workspace/memory").mkdir(parents=True)
            (archive_root / "openclaw.json").write_text(
                '{"source":"archive"}\n', encoding="utf-8"
            )
            (archive_root / "devices/new.json").write_text("archive-device\n")
            execution_canary = root / "archive-automation-executed"
            automation_payload = json.dumps(
                {"command": f"touch {execution_canary}", "secretRef": "provider/key"}
            ) + "\n"
            (archive_root / "cron/jobs.json").write_text(
                automation_payload, encoding="utf-8"
            )
            (archive_root / "tasks/task.json").write_text(
                automation_payload, encoding="utf-8"
            )
            (archive_root / "flows/flow.json").write_text(
                automation_payload, encoding="utf-8"
            )
            (archive_root / "workspace/memory/restored.md").write_text(
                "restored-memory\n", encoding="utf-8"
            )
            plaintext = root / "legacy.tar.gz"
            archive = root / "legacy.tar.gz.gpg"
            with tarfile.open(plaintext, "w:gz") as output:
                output.add(archive_root / "openclaw.json", arcname="openclaw.json")
                output.add(archive_root / "devices", arcname="devices")
                output.add(archive_root / "cron", arcname="cron")
                output.add(archive_root / "tasks", arcname="tasks")
                output.add(archive_root / "flows", arcname="flows")
                output.add(archive_root / "workspace/memory", arcname="workspace/memory")
            crypto_environment = encrypt_restore_fixture(root, plaintext, archive)

            destination = root / "destination"
            destination.mkdir(mode=0o700)
            authorities = {
                "openclaw.json": "owner-config\n",
                "secrets.json": "owner-secrets\n",
                ".env": "owner-env\n",
                "credentials/owner": "owner-credential\n",
                "identity/owner": "owner-identity\n",
                "devices/paired.json": "owner-device\n",
                "cron/jobs.json": "owner-cron\n",
                "tasks/owner.json": "owner-task\n",
                "flows/owner.json": "owner-flow\n",
            }
            for relative, payload in authorities.items():
                path = destination / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(payload, encoding="utf-8")
            rollback_marker = root / "rollback-code-ran"
            rollback_script = destination / "workspace/scripts/rollback_task.sh"
            rollback_script.parent.mkdir(parents=True)
            rollback_script.write_text(
                f"#!/bin/sh\ntouch {rollback_marker}\n", encoding="utf-8"
            )
            rollback_script.chmod(0o700)
            for queue in ("send-queue", "job-queue", "manim-queue", "email-queue"):
                queue_path = destination / "workspace/data" / queue
                queue_path.mkdir(parents=True)
                (queue_path / "action.json").write_text(
                    f'{{"queue":"{queue}"}}\n', encoding="utf-8"
                )

            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_gpg = fake_bin / "gpg"
            fake_gpg_canary = root / "path-gpg-ran"
            fake_gpg.write_text(
                f"#!/bin/sh\ntouch {fake_gpg_canary}\nexit 91\n",
                encoding="utf-8",
            )
            fake_gpg.chmod(0o700)
            result = subprocess.run(
                [
                    str(ROOT / "restore.sh"),
                    "--archive",
                    str(archive),
                    "--prefix",
                    str(destination),
                    "--overlay-only",
                    "--allow-legacy",
                    "--skip-services",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    **crypto_environment,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(fake_gpg_canary.exists())
            for relative, payload in authorities.items():
                self.assertEqual((destination / relative).read_text(), payload)
            self.assertFalse((destination / "devices/new.json").exists())
            self.assertEqual(
                (destination / "workspace/memory/restored.md").read_text(),
                "restored-memory\n",
            )
            self.assertFalse(rollback_marker.exists())
            self.assertFalse(execution_canary.exists())
            archive_quarantines = list(
                (destination / "recovery-quarantine/archive-authority").glob("*/payload")
            )
            self.assertEqual(len(archive_quarantines), 1)
            for relative in (
                "openclaw.json",
                "devices/new.json",
                "cron/jobs.json",
                "tasks/task.json",
                "flows/flow.json",
            ):
                self.assertTrue((archive_quarantines[0] / relative).is_file())
            for queue in ("send-queue", "job-queue", "manim-queue", "email-queue"):
                queue_path = destination / "workspace/data" / queue
                self.assertEqual(list(queue_path.iterdir()), [])
                self.assertEqual(queue_path.stat().st_mode & 0o777, 0o700)
            quarantine_roots = list(
                (destination / "recovery-quarantine/action-queues").glob("*")
            )
            self.assertEqual(len(quarantine_roots), 1)
            for queue in ("send-queue", "job-queue", "manim-queue", "email-queue"):
                self.assertTrue((quarantine_roots[0] / queue / "action.json").is_file())
            rollback_roots = list(root.glob("destination.restore-rollback-*-*"))
            self.assertEqual(len(rollback_roots), 1)

            transaction = ROOT / "scripts/restore_transaction.py"
            refused = subprocess.run(
                [
                    sys.executable,
                    str(transaction),
                    "replay-queues",
                    "--prefix",
                    str(destination),
                    "--quarantine",
                    str(quarantine_roots[0]),
                    "--confirm",
                    "WRONG",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(refused.returncode, 2)
            unapproved = subprocess.run(
                [
                    sys.executable,
                    str(transaction),
                    "replay-queues",
                    "--prefix",
                    str(destination),
                    "--quarantine",
                    str(quarantine_roots[0]),
                    "--confirm",
                    "REPLAY_QUARANTINED_ACTIONS",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(unapproved.returncode, 2)
            approved = subprocess.run(
                [
                    sys.executable,
                    str(transaction),
                    "approve-queues",
                    "--prefix",
                    str(destination),
                    "--quarantine",
                    str(quarantine_roots[0]),
                    "--confirm",
                    "REVIEW_QUARANTINED_ACTIONS",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
            collision = destination / "workspace/data/send-queue/action.json"
            collision.write_text('{"queue":"concurrent"}\n', encoding="utf-8")
            collision.chmod(0o600)
            collision_result = subprocess.run(
                [
                    sys.executable,
                    str(transaction),
                    "replay-queues",
                    "--prefix",
                    str(destination),
                    "--quarantine",
                    str(quarantine_roots[0]),
                    "--confirm",
                    "REPLAY_QUARANTINED_ACTIONS",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(collision_result.returncode, 2)
            self.assertEqual(collision.read_text(), '{"queue":"concurrent"}\n')
            # Replay is item-transactional and journal-resumable: queues ordered
            # before the collision may already be durably published, while the
            # colliding source remains quarantined and untouched.
            for queue in ("email-queue", "job-queue", "manim-queue"):
                self.assertTrue(
                    (destination / "workspace/data" / queue / "action.json").is_file()
                )
                self.assertFalse(
                    (quarantine_roots[0] / queue / "action.json").exists()
                )
            self.assertTrue(
                (quarantine_roots[0] / "send-queue/action.json").is_file()
            )
            collision.unlink()
            replayed = subprocess.run(
                [
                    sys.executable,
                    str(transaction),
                    "replay-queues",
                    "--prefix",
                    str(destination),
                    "--quarantine",
                    str(quarantine_roots[0]),
                    "--confirm",
                    "REPLAY_QUARANTINED_ACTIONS",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            for queue in ("send-queue", "job-queue", "manim-queue", "email-queue"):
                self.assertTrue(
                    (destination / "workspace/data" / queue / "action.json").is_file()
                )
                self.assertFalse(
                    (quarantine_roots[0] / queue / "action.json").exists()
                )
            journal = (
                quarantine_roots[0] / ".replay-journal.jsonl"
            ).read_text(encoding="utf-8")
            self.assertIn('"status": "prepared"', journal)
            self.assertIn('"status": "published"', journal)

    def test_restore_failure_before_commit_leaves_target_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.write_text("new-memory\n", encoding="utf-8")
            plaintext = root / "legacy.tar.gz"
            archive = root / "legacy.tar.gz.gpg"
            with tarfile.open(plaintext, "w:gz") as output:
                output.add(payload, arcname="workspace/memory/new.md")
            crypto_environment = encrypt_restore_fixture(root, plaintext, archive)
            destination = root / "destination"
            destination.mkdir(mode=0o700)
            sentinel = destination / "sentinel"
            sentinel.write_text("original\n", encoding="utf-8")
            (destination / "workspace").mkdir()
            (destination / "workspace/memory").write_text(
                "directory-collision\n", encoding="utf-8"
            )
            before = sentinel.stat()
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_gpg = fake_bin / "gpg"
            fake_gpg_canary = root / "path-gpg-ran"
            fake_gpg.write_text(
                f"#!/bin/sh\ntouch {fake_gpg_canary}\nexit 91\n",
                encoding="utf-8",
            )
            fake_gpg.chmod(0o700)
            result = subprocess.run(
                [
                    str(ROOT / "restore.sh"),
                    "--archive",
                    str(archive),
                    "--prefix",
                    str(destination),
                    "--overlay-only",
                    "--allow-legacy",
                    "--skip-services",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    **os.environ,
                    **crypto_environment,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(fake_gpg_canary.exists())
            after = sentinel.stat()
            self.assertEqual(sentinel.read_text(), "original\n")
            self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
            self.assertEqual(list(root.glob("destination.restore-rollback-*")), [])

    def test_atomic_commit_retains_complete_rollback_after_caller_exit(self) -> None:
        helper = ROOT / "scripts/restore_transaction.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "state"
            candidate = root / ".state.restore-stage.fixture"
            rollback = root / "state.restore-rollback-fixture"
            for directory, value in ((target, "old"), (candidate, "new")):
                (directory / "workspace/data").mkdir(parents=True)
                directory.chmod(0o700)
                (directory / "value").write_text(value, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "empty-queues",
                    "--candidate",
                    str(candidate),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            committed = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "commit",
                    "--candidate",
                    str(candidate),
                    "--target",
                    str(target),
                    "--rollback",
                    str(rollback),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(committed.returncode, 0, committed.stderr)
            self.assertEqual((target / "value").read_text(), "new")
            self.assertEqual((rollback / "value").read_text(), "old")

    def test_atomic_commit_rejects_parent_candidate_and_target_identity_changes(self) -> None:
        helper = ROOT / "scripts/restore_transaction.py"
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent.chmod(0o700)
            target = parent / "target"
            candidate = parent / "candidate"
            target.mkdir(mode=0o700)
            candidate.mkdir(mode=0o700)
            (target / "sentinel").write_text("old\n", encoding="utf-8")
            (candidate / "sentinel").write_text("new\n", encoding="utf-8")
            parent_info = parent.stat()
            target_info = target.stat()
            candidate_info = candidate.stat()
            base = [
                sys.executable,
                str(helper),
                "commit",
                "--candidate",
                str(candidate),
                "--target",
                str(target),
                "--expected-parent-device",
                str(parent_info.st_dev),
                "--expected-parent-inode",
                str(parent_info.st_ino),
                "--expected-candidate-device",
                str(candidate_info.st_dev),
                "--expected-candidate-inode",
                str(candidate_info.st_ino),
                "--expected-target-device",
                str(target_info.st_dev),
                "--expected-target-inode",
                str(target_info.st_ino),
            ]
            for index, (flag, value) in enumerate(
                (
                    ("--expected-parent-inode", parent_info.st_ino + 1),
                    ("--expected-candidate-inode", candidate_info.st_ino + 1),
                    ("--expected-target-inode", target_info.st_ino + 1),
                )
            ):
                with self.subTest(flag=flag):
                    argv = list(base)
                    argv[argv.index(flag) + 1] = str(value)
                    argv.extend(["--rollback", str(parent / f"rollback-{index}")])
                    result = subprocess.run(
                        argv,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual((target / "sentinel").read_text(), "old\n")
                    self.assertEqual((candidate / "sentinel").read_text(), "new\n")

    def test_atomic_commit_exchange_failure_keeps_old_target_published(self) -> None:
        helper = ROOT / "scripts/restore_transaction.py"
        specification = importlib.util.spec_from_file_location(
            "restore_transaction_for_test", helper
        )
        assert specification is not None and specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "state"
            candidate = root / ".state.restore-stage.fixture"
            rollback = root / "state.restore-rollback-fixture"
            for directory, value in ((target, "old"), (candidate, "new")):
                (directory / "workspace/data").mkdir(parents=True)
                directory.chmod(0o700)
                (directory / "value").write_text(value, encoding="utf-8")
            module.create_empty_action_queues(candidate)
            with mock.patch.object(
                module,
                "_rename_exchange",
                side_effect=module.RestoreTransactionError("simulated exchange failure"),
            ):
                with self.assertRaises(module.RestoreTransactionError):
                    module.commit_candidate(candidate, target, rollback)
            self.assertEqual((target / "value").read_text(), "old")
            self.assertEqual((rollback / "value").read_text(), "new")
            self.assertFalse(candidate.exists())

    def test_calibre_runtime_is_bounded_and_does_not_install_on_use(self) -> None:
        gdrive = (ROOT / "workspace/skills/calibre/lib/gdrive.py").read_text(
            encoding="utf-8"
        )
        sync = (ROOT / "workspace/skills/calibre/lib/drive_sync.py").read_text(
            encoding="utf-8"
        )
        runner = (ROOT / "workspace/skills/calibre/run_cal.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("CALIBRE_HTTP_TIMEOUT_SECONDS", gdrive)
        self.assertIn("num_retries=self.retries", gdrive)
        self.assertIn('find_root_file("metadata.db")', sync)
        self.assertIn("PRAGMA quick_check", sync)
        pull_db = sync.split("def pull_db", 1)[1].split("def push_db", 1)[0]
        self.assertNotIn('search("metadata.db"', pull_db)
        self.assertNotIn("pip install", runner)

    def test_calibre_pull_publishes_only_a_valid_sqlite_database(self) -> None:
        skill = ROOT / "workspace/skills/calibre"
        sys.path.insert(0, str(skill))
        try:
            from lib.drive_sync import DriveSync
        finally:
            sys.path.pop(0)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO books DEFAULT VALUES")
            connection.commit()
            connection.close()

            destination = root / "cache/metadata.db"

            class Client:
                def __init__(self) -> None:
                    self.lookups = 0

                def find_root_file(self, name: str) -> dict[str, str]:
                    self.lookups += 1
                    self.test_name = name
                    return {
                        "id": "fixture",
                        "name": name,
                        "modifiedTime": "fixture-time",
                        "size": str(source.stat().st_size),
                    }

                def download_file(self, _file_id, path, **_kwargs) -> None:
                    shutil.copyfile(source, path)

            client = Client()
            sync = DriveSync(
                {
                    "gdrive_folder_id": "root",
                    "db_local_path": str(destination),
                    "staging_dir": str(root / "stage"),
                }
            )
            sync._client = client

            self.assertTrue(sync.pull_db(force=True))
            self.assertEqual(client.lookups, 1)
            self.assertEqual(client.test_name, "metadata.db")
            self.assertEqual(
                sqlite3.connect(destination).execute("PRAGMA quick_check").fetchone(),
                ("ok",),
            )


if __name__ == "__main__":
    unittest.main()
