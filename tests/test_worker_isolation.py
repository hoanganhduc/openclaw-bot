from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
UNIT_DIRECTORY = ROOT / "systemd" / "user"
WORKER_UNITS = {
    "telegram": "send-queue-worker.service",
    "zulip": "openclaw-zulip-delivery-worker.service",
    "googlechat": "openclaw-googlechat-delivery-worker.service",
    "whatsapp": "openclaw-whatsapp-delivery-worker.service",
    "zalo": "openclaw-zalo-delivery-worker.service",
    "sage": "openclaw-sage-worker.service",
    "manim": "openclaw-manim-worker.service",
    "email": "openclaw-email-worker.service",
}
DELIVERY_CHANNELS = frozenset(
    {"telegram", "zulip", "googlechat", "whatsapp", "zalo"}
)


def load_service_transaction():
    specification = importlib.util.spec_from_file_location(
        "service_transaction_worker_test", ROOT / "scripts" / "service_transaction.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_module(name: str, relative: str):
    specification = importlib.util.spec_from_file_location(
        name, ROOT / relative
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class WorkerIsolationTests(unittest.TestCase):
    def test_workers_are_split_hardened_and_resource_bounded(self) -> None:
        texts = {
            kind: (UNIT_DIRECTORY / name).read_text(encoding="utf-8")
            for kind, name in WORKER_UNITS.items()
        }
        runtime_directories: set[str] = set()
        for kind, text in texts.items():
            with self.subTest(kind=kind):
                queue_kind = "send" if kind in DELIVERY_CHANNELS else kind
                self.assertIn(f"Environment=OPENCLAW_QUEUE_KIND={queue_kind}", text)
                self.assertIn("--artifact owner_state_lock.py -- --mode shared", text)
                self.assertIn("--artifact job_queue_worker.sh", text)
                self.assertIn("ProtectHome=tmpfs", text)
                self.assertIn("ProtectSystem=strict", text)
                self.assertIn("NoNewPrivileges=true", text)
                self.assertIn("PrivateDevices=true", text)
                self.assertIn("CapabilityBoundingSet=\n", text)
                self.assertIn("MemoryMax=", text)
                self.assertIn("MemorySwapMax=", text)
                self.assertIn("TasksMax=", text)
                self.assertIn("CPUQuota=", text)
                self.assertIn(
                    "BindReadOnlyPaths={{ OPENCLAW_LIBEXEC_ROOT }}", text
                )
                self.assertIn(
                    "Environment=OPENCLAW_EXPECTED_OWNER_STATE_LOCK={{ OWNER_STATE_LOCK }}",
                    text,
                )
                self.assertNotIn(
                    "BindReadOnlyPaths={{ OPENCLAW_LIBEXEC }}", text
                )
                runtime = next(
                    line.split("=", 1)[1]
                    for line in text.splitlines()
                    if line.startswith("RuntimeDirectory=")
                )
                self.assertNotIn(runtime, runtime_directories)
                runtime_directories.add(runtime)

        self.assertIn("PrivateNetwork=true", texts["sage"])
        self.assertIn("PrivateNetwork=true", texts["manim"])
        self.assertIn("RestrictAddressFamilies=AF_UNIX", texts["manim"])
        self.assertNotIn("AF_INET", texts["manim"])
        for channel in DELIVERY_CHANNELS:
            text = texts[channel]
            self.assertNotIn("InaccessiblePaths={{ OPENCLAW_WORKSPACE }}", text)
            for mount in (
                "{{ USER_HOME }}",
                "{{ USER_HOME }}/.local",
                "{{ USER_HOME }}/.local/state",
                "{{ USER_HOME }}/.local/state/openclaw-bot",
            ):
                self.assertIn(f"TemporaryFileSystem={mount}:mode=0700", text)
            self.assertIn(
                f"Environment=OPENCLAW_DELIVERY_CHANNEL={channel}", text
            )
            self.assertIn(
                f"{{{{ OPENCLAW_WORKSPACE }}}}/data/send-queue/{channel}", text
            )
            self.assertIn(
                "LoadCredential=file-delivery-policy:{{ OPENCLAW_HOME }}/file-delivery-policy.json",
                text,
            )
            self.assertNotIn("BindPaths={{ OPENCLAW_HOME }}", text)
            self.assertNotIn("BindReadOnlyPaths={{ OPENCLAW_HOME }}", text)
            self.assertNotIn(
                "Environment=OPENCLAW_STATE_DIR={{ OPENCLAW_HOME }}", text
            )
            self.assertNotIn(".config/send-email", text)
            self.assertNotIn(".gnupg", text)

        self.assertIn("LoadCredential=telegram-token:", texts["telegram"])
        self.assertIn("OPENCLAW_TELEGRAM_CREDENTIAL=%d/telegram-token", texts["telegram"])
        self.assertNotIn("OPENCLAW_DELIVERY_STATE=", texts["telegram"])
        self.assertNotIn(".npm-global", texts["telegram"])
        for channel in DELIVERY_CHANNELS - {"telegram"}:
            text = texts[channel]
            authority = (
                "{{ USER_HOME }}/.local/state/openclaw-bot/"
                f"delivery-authorities/{channel}"
            )
            self.assertIn(f"Environment=OPENCLAW_DELIVERY_STATE={authority}", text)
            self.assertIn(f"BindReadOnlyPaths={authority}", text)
            self.assertIn(
                "BindReadOnlyPaths={{ USER_HOME }}/.npm-global/lib/node_modules/openclaw",
                text,
            )
            for sealed in ("node-generations", "npm-closures"):
                self.assertIn(
                    "BindReadOnlyPaths=-{{ USER_HOME }}/.local/share/coding-system/"
                    + sealed
                    + "\n",
                    text,
                )
            self.assertNotIn("telegram-token", text)
            for other in DELIVERY_CHANNELS - {"telegram", channel}:
                self.assertNotIn(f"delivery-authorities/{other}", text)
        self.assertNotIn("{{ OPENCLAW_HOME }}/email-approvals", texts["manim"])
        self.assertNotIn("{{ OPENCLAW_HOME }}/email-approvals", texts["sage"])
        self.assertIn(
            "BindReadOnlyPaths=-{{ USER_HOME }}/.local/share/manim-math-animation-venv",
            texts["manim"],
        )
        self.assertIn("{{ OPENCLAW_HOME }}/email-approvals", texts["email"])
        self.assertIn("{{ USER_HOME }}/.config/send-email", texts["email"])
        self.assertIn("{{ USER_HOME }}/.gnupg", texts["email"])

    def test_service_templates_render_without_unresolved_capabilities(self) -> None:
        module = load_service_transaction()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = module.collect(
                UNIT_DIRECTORY,
                prefix=root / "state",
                home=root / "home",
                libexec=(
                    root
                    / "home/.local/libexec/openclaw-bot/generations"
                    / ("a" * 64)
                ),
            )
        rendered = {relative.as_posix(): payload.decode() for relative, payload, _ in files}
        for name in WORKER_UNITS.values():
            self.assertIn(name, rendered)
            self.assertNotIn("{{", rendered[name])
            self.assertNotIn("}}", rendered[name])

    def test_attested_runtime_contains_both_queue_boundary_helpers(self) -> None:
        module = load_service_transaction()
        artifacts = {destination: source for destination, source, _ in module.HOST_ARTIFACTS}
        self.assertEqual(artifacts["queue_boundary.py"], "scripts/queue_boundary.py")
        self.assertEqual(artifacts["email_delivery.py"], "scripts/email_delivery.py")
        launcher = (ROOT / "scripts" / "host_exec.py").read_text(encoding="utf-8")
        self.assertIn('"OPENCLAW_QUEUE_KIND"', launcher)
        self.assertNotIn('"MMA_PYTHON"', launcher)
        worker = (ROOT / "workspace" / "scripts" / "job_queue_worker.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('case "$QUEUE_KIND" in', worker)
        self.assertNotIn("OPENCLAW_MANIM_PYTHON", worker)
        generation, collected, manifest = module._collect_host_artifacts(ROOT, {})
        self.assertEqual(len(generation), 64)
        self.assertEqual(len(collected), len(module.HOST_ARTIFACTS))
        self.assertEqual(json.loads(manifest)["generation"], generation)

    def test_service_state_bootstrap_is_private_deny_default_and_preserving(self) -> None:
        module = load_service_transaction()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            prefix = home / ".openclaw"
            repository = root / "repository"
            (prefix / "workspace").mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            prefix.chmod(0o700)
            (repository / "config").mkdir(parents=True, mode=0o700)
            for name in (
                "email-policy.json.template",
                "file-delivery-policy.json.template",
            ):
                template = repository / "config" / name
                template.write_bytes((ROOT / "config" / name).read_bytes())
                template.chmod(0o600)
            (prefix / "secrets.json").write_text(
                json.dumps(
                    {
                        "TELEGRAM_BOT_TOKEN": "exact-token-canary",
                        "OPENAI_API_KEY": "must-not-be-projected",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (prefix / "secrets.json").chmod(0o600)
            (prefix / "openclaw.json").write_text(
                json.dumps(
                    {
                        "channels": {
                            "telegram": {
                                "botToken": {
                                    "id": "/TELEGRAM_BOT_TOKEN",
                                    "provider": "canonical",
                                    "source": "file",
                                },
                                "enabled": True,
                            },
                            "zulip": {
                                "apiKey": {
                                    "command": "must-not-execute",
                                    "source": "exec",
                                },
                                "enabled": False,
                            },
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (prefix / "openclaw.json").chmod(0o600)

            module._prepare_service_state(prefix, home, repository)
            policy = prefix / "email-approvals" / "policy.json"
            self.assertEqual(
                json.loads(policy.read_text()),
                {
                    "schema": "openclaw.email-policy/v1",
                    "email_policy": {"approved_messages": []},
                },
            )
            self.assertEqual(policy.stat().st_mode & 0o777, 0o600)
            file_policy = prefix / "file-delivery-policy.json"
            self.assertEqual(
                json.loads(file_policy.read_text()),
                {
                    "schema": "openclaw.file-delivery-policy/v1",
                    "delivery_policy": {
                        "allowed_targets": {
                            "telegram": [],
                            "zulip": [],
                            "googlechat": [],
                            "whatsapp": [],
                            "zalo": [],
                        }
                    },
                },
            )
            self.assertEqual(file_policy.stat().st_mode & 0o777, 0o600)
            authority_root = (
                home / ".local/state/openclaw-bot/delivery-authorities"
            )
            token = authority_root / "telegram" / "token"
            self.assertEqual(token.read_text(), "exact-token-canary")
            self.assertNotIn("must-not-be-projected", token.read_text())
            self.assertEqual(token.stat().st_mode & 0o777, 0o400)
            lock = home / ".openclaw.owner-state.lock"
            self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
            for relative in (
                "workspace/data/send-queue",
                "workspace/data/send-queue/telegram",
                "workspace/data/send-queue/zulip",
                "workspace/data/send-queue/googlechat",
                "workspace/data/send-queue/whatsapp",
                "workspace/data/send-queue/zalo",
                "workspace/data/job-queue",
                "workspace/data/manim-queue",
                "workspace/data/email-queue",
                "workspace/data/research/sagemath",
                "workspace/data/research/manim",
                "workspace/data/research/email",
            ):
                self.assertEqual((prefix / relative).stat().st_mode & 0o777, 0o700)
            for channel in DELIVERY_CHANNELS:
                self.assertEqual(
                    (authority_root / channel).stat().st_mode & 0o777, 0o700
                )
                status = json.loads(
                    (authority_root / channel / "STATUS.json").read_text()
                )
                self.assertEqual(status["channel"], channel)
                self.assertEqual(
                    status["status"],
                    "CONFIGURED" if channel == "telegram" else "NOT_CONFIGURED",
                )

            approved = {
                "schema": "openclaw.email-policy/v1",
                "email_policy": {"approved_messages": [{"preserve": True}]},
            }
            policy.write_text(json.dumps(approved) + "\n", encoding="utf-8")
            policy.chmod(0o600)
            legacy_token = authority_root / "telegram-token"
            legacy_token.write_text("old-projection", encoding="utf-8")
            legacy_token.chmod(0o600)
            module._prepare_service_state(prefix, home, repository)
            self.assertEqual(json.loads(policy.read_text()), approved)
            self.assertFalse(legacy_token.exists())

    def test_owner_archive_projects_minimal_channel_authorities_and_workers_consume_them(self) -> None:
        service = load_service_transaction()
        owner_archive = load_module(
            "owner_archive_projection_test", "scripts/owner_archive.py"
        )
        delivery = load_module(
            "file_delivery_projection_test", "scripts/file_delivery.py"
        )
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir(mode=0o700)
            source = root / "old-owner-state"
            source.mkdir(mode=0o700)
            canonical_google = home / ".config/openclaw/google-chat"
            canonical_google.mkdir(parents=True, mode=0o700)
            for parent in (
                home / ".config",
                home / ".config/openclaw",
                canonical_google,
            ):
                parent.chmod(0o700)
            service_account = canonical_google / "offline-service-account.json"
            service_account.write_text(
                json.dumps(
                    {
                        "type": "service_account",
                        "project_id": "offline-project",
                        "private_key_id": "offline-key-id",
                        "private_key": (
                            "-----BEGIN " + "PRIVATE KEY-----\n"
                            "b2ZmbGluZS1maXh0dXJl\n"
                            "-----END " + "PRIVATE KEY-----\n"
                        ),
                        "client_email": "offline@example.invalid",
                        "client_id": "100000000000000000000",  # LEAKSCAN-EXEMPT: synthetic fixture
                        "auth_uri": "https://accounts.example.invalid/auth",
                        "token_uri": "https://oauth.example.invalid/token",
                        "auth_provider_x509_cert_url": (
                            "https://oauth.example.invalid/certs"
                        ),
                        "client_x509_cert_url": (
                            "https://oauth.example.invalid/client-cert"
                        ),
                        "universe_domain": "example.invalid",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            service_account.chmod(0o600)

            secret_refs = {
                "telegram": ("botToken", "TELEGRAM_BOT_TOKEN"),
                "zulip": ("apiKey", "ZULIP_API_KEY"),
                "zalo": ("botToken", "ZALO_BOT_TOKEN"),
            }
            channels: dict[str, object] = {
                "telegram": {"enabled": True},
                "zulip": {
                    "enabled": True,
                    "email": "bot@example.invalid",
                    "url": "https://chat.example.invalid",
                },
                "googlechat": {
                    "enabled": True,
                    "serviceAccountFile": str(service_account),
                },
                "whatsapp": {
                    "enabled": True,
                    "dmPolicy": "allowlist",
                },
                "zalo": {"enabled": True},
            }
            for channel, (field, secret_key) in secret_refs.items():
                assert isinstance(channels[channel], dict)
                channels[channel][field] = {
                    "id": f"/{secret_key}",
                    "provider": "canonical",
                    "source": "file",
                }
            (source / "openclaw.json").write_text(
                json.dumps({"channels": channels}) + "\n",
                encoding="utf-8",
            )
            (source / "openclaw.json").chmod(0o600)
            (source / "secrets.json").write_text(
                json.dumps(
                    {
                        "TELEGRAM_BOT_TOKEN": "telegram-offline-token",
                        "ZULIP_API_KEY": "zulip-offline-key",
                        "ZALO_BOT_TOKEN": "zalo-offline-token",
                        "OPENAI_API_KEY": "unrelated-secret-must-not-project",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (source / "secrets.json").chmod(0o600)
            policy_document = {
                "schema": "openclaw.file-delivery-policy/v1",
                "delivery_policy": {
                    "allowed_targets": {
                        channel: ["offline-approved-target"]
                        for channel in DELIVERY_CHANNELS
                    }
                },
            }
            (source / "file-delivery-policy.json").write_text(
                json.dumps(policy_document) + "\n",
                encoding="utf-8",
            )
            (source / "file-delivery-policy.json").chmod(0o600)
            whatsapp = source / "credentials/whatsapp/default"
            whatsapp.mkdir(parents=True, mode=0o700)
            for parent in (
                source / "credentials",
                source / "credentials/whatsapp",
                whatsapp,
            ):
                parent.chmod(0o700)
            for name, payload in (
                ("creds.json", {"fixture": "offline-session"}),
                ("session-key.json", {"fixture": "offline-key"}),
            ):
                member = whatsapp / name
                member.write_text(json.dumps(payload) + "\n", encoding="utf-8")
                member.chmod(0o600)

            remote_bridge = home / ".local/state/remote-bridge"
            remote_bridge.mkdir(parents=True, mode=0o700)
            for parent in (
                home / ".local",
                home / ".local/state",
                remote_bridge,
            ):
                parent.chmod(0o700)
            remote_canary = remote_bridge / "zulip-control.json"
            remote_canary.write_text(
                '{"credential":"remote-bridge-must-not-project"}\n',  # LEAKSCAN-EXEMPT: synthetic fixture
                encoding="utf-8",
            )
            remote_canary.chmod(0o600)

            native = root / "native.tar.gz"
            native_manifest = (
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "archiveRoot": "native",
                        "createdAt": "2026-08-05T00:00:00Z",
                        "runtimeVersion": "offline-fixture",
                        "assets": [
                            {
                                "kind": "state",
                                "sourcePath": str(source),
                                "archivePath": "native/state",
                            }
                        ],
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            with tarfile.open(native, "w:gz") as archive:
                info = tarfile.TarInfo("native/manifest.json")
                info.mode = 0o600
                info.size = len(native_manifest)
                archive.addfile(info, io.BytesIO(native_manifest))
            native.chmod(0o600)
            output = root / "archive-output"
            output.mkdir(mode=0o700)
            archive_path = output / "owner.tar.gz"
            owner_archive.build_owner_archive(native, source, archive_path)
            extracted = root / "extracted"
            extracted.mkdir(mode=0o700)
            owner_archive.extract_owner_archive(
                archive_path,
                extracted,
                allow_legacy=False,
                expected_runtime_version="offline-fixture",
            )
            restored = (
                extracted
                / "recovery-quarantine/archive-authority/payload"
            )
            repository = root / "repository"
            (repository / "config").mkdir(parents=True, mode=0o700)
            for name in (
                "email-policy.json.template",
                "file-delivery-policy.json.template",
            ):
                target = repository / "config" / name
                target.write_bytes((ROOT / "config" / name).read_bytes())
                target.chmod(0o600)

            service._prepare_service_state(restored, home, repository)
            authority_root = (
                home / ".local/state/openclaw-bot/delivery-authorities"
            )
            all_projected = b"".join(
                path.read_bytes()
                for path in authority_root.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(b"unrelated-secret-must-not-project", all_projected)
            self.assertNotIn(b"remote-bridge-must-not-project", all_projected)
            for channel in DELIVERY_CHANNELS:
                projection = authority_root / channel
                self.assertEqual(projection.stat().st_mode & 0o777, 0o700)
                status = json.loads((projection / "STATUS.json").read_text())
                self.assertEqual(status["status"], "CONFIGURED")
                for projected_file in projection.rglob("*"):
                    if projected_file.is_file():
                        self.assertEqual(projected_file.stat().st_mode & 0o777, 0o400)
                if channel != "telegram":
                    projected_config = json.loads(
                        (projection / "openclaw.json").read_text()
                    )
                    self.assertEqual(set(projected_config["channels"]), {channel})
                    self.assertEqual(projected_config["plugins"]["allow"], [channel])
            self.assertEqual(
                (authority_root / "telegram/token").read_text(),
                "telegram-offline-token",
            )
            self.assertEqual(
                json.loads((authority_root / "zulip/secrets.json").read_text()),
                {"ZULIP_API_KEY": "zulip-offline-key"},
            )
            self.assertEqual(
                json.loads((authority_root / "zalo/secrets.json").read_text()),
                {"ZALO_BOT_TOKEN": "zalo-offline-token"},
            )
            self.assertEqual(
                {
                    path.name
                    for path in (
                        authority_root / "whatsapp/credentials/whatsapp/default"
                    ).iterdir()
                },
                {"creds.json", "session-key.json"},
            )
            openclaw_cli = Path.home() / ".npm-global/bin/openclaw"
            if openclaw_cli.is_file():
                for channel in DELIVERY_CHANNELS - {"telegram"}:
                    projection = authority_root / channel
                    validated = subprocess.run(
                        [str(openclaw_cli), "config", "validate", "--json"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=30,
                        check=False,
                        env={
                            "HOME": str(home),
                            "OPENCLAW_CONFIG_PATH": str(
                                projection / "openclaw.json"
                            ),
                            "OPENCLAW_STATE_DIR": str(projection),
                            "PATH": "/usr/bin:/bin",
                        },
                    )
                    self.assertEqual(
                        validated.returncode,
                        0,
                        f"{channel}: {validated.stderr}",
                    )

            export = restored / "workspace/data/exports/offline-evidence.bin"
            export.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            export.write_bytes(b"offline-descriptor-bound-evidence")
            export.chmod(0o600)
            observed: list[str] = []

            def sender(**kwargs: object) -> None:
                observed.append(str(kwargs["channel"]))

            with mock.patch.object(
                delivery.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(home)),
            ):
                for channel in sorted(DELIVERY_CHANNELS):
                    job = (
                        restored
                        / "workspace/data/send-queue"
                        / channel
                        / f"offline-{channel}.working"
                    )
                    job.write_text(
                        json.dumps(
                            {
                                "schema": "openclaw.send-queue-job/v1",
                                "id": f"offline-{channel}",
                                "channel": channel,
                                "target": "offline-approved-target",
                                "media": "/workspace/data/exports/offline-evidence.bin",
                                "caption": "offline",
                                "status": "pending",
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    job.chmod(0o600)
                    arguments: dict[str, object] = {
                        "workspace": restored / "workspace",
                        "policy_path": restored / "file-delivery-policy.json",
                        "expected_channel": channel,
                        "job_path": job,
                        "sender": sender,
                    }
                    if channel == "telegram":
                        arguments["telegram_credential"] = (
                            authority_root / "telegram/token"
                        )
                    else:
                        arguments["channel_state"] = authority_root / channel
                    result = delivery.process_job(**arguments)
                    self.assertEqual(result["status"], "ok")
            self.assertEqual(set(observed), set(DELIVERY_CHANNELS))

            restored_secrets = json.loads(
                (restored / "secrets.json").read_text(encoding="utf-8")
            )
            restored_secrets["TELEGRAM_BOT_TOKEN"] = "rotated-offline-token"
            (restored / "secrets.json").write_text(
                json.dumps(restored_secrets) + "\n",
                encoding="utf-8",
            )
            (restored / "secrets.json").chmod(0o600)
            stale = authority_root / "zulip/stale-generated-member"
            stale.write_text("discard", encoding="utf-8")
            stale.chmod(0o400)
            legacy = authority_root / "telegram-token"
            legacy.write_text("legacy-token", encoding="utf-8")
            legacy.chmod(0o600)
            service._prepare_service_state(restored, home, repository)
            self.assertEqual(
                (authority_root / "telegram/token").read_text(),
                "rotated-offline-token",
            )
            self.assertFalse(stale.exists())
            self.assertFalse(legacy.exists())
            self.assertEqual(
                [
                    path.name
                    for path in authority_root.iterdir()
                    if path.name.startswith(".")
                ],
                [],
            )

    def test_projection_validation_failure_preserves_every_live_channel(self) -> None:
        service = load_service_transaction()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            prefix = home / ".openclaw"
            repository = root / "repository"
            (prefix / "workspace").mkdir(parents=True, mode=0o700)
            home.chmod(0o700)
            prefix.chmod(0o700)
            (repository / "config").mkdir(parents=True, mode=0o700)
            for name in (
                "email-policy.json.template",
                "file-delivery-policy.json.template",
            ):
                target = repository / "config" / name
                target.write_bytes((ROOT / "config" / name).read_bytes())
                target.chmod(0o600)

            def write_config(*, whatsapp: bool) -> None:
                channels: dict[str, object] = {
                    "telegram": {
                        "enabled": True,
                        "botToken": {
                            "id": "/TELEGRAM_BOT_TOKEN",
                            "provider": "canonical",
                            "source": "file",
                        },
                    }
                }
                if whatsapp:
                    channels["whatsapp"] = {"enabled": True}
                (prefix / "openclaw.json").write_text(
                    json.dumps({"channels": channels}) + "\n",
                    encoding="utf-8",
                )
                (prefix / "openclaw.json").chmod(0o600)

            def write_secret(value: str) -> None:
                (prefix / "secrets.json").write_text(
                    json.dumps({"TELEGRAM_BOT_TOKEN": value}) + "\n",
                    encoding="utf-8",
                )
                (prefix / "secrets.json").chmod(0o600)

            write_config(whatsapp=False)
            write_secret("original-token")
            service._prepare_service_state(prefix, home, repository)
            authority_root = (
                home / ".local/state/openclaw-bot/delivery-authorities"
            )
            token = authority_root / "telegram/token"
            self.assertEqual(token.read_text(), "original-token")

            write_config(whatsapp=True)
            write_secret("must-not-publish")
            with self.assertRaisesRegex(
                service.ServiceTransactionError,
                "WhatsApp session authority is missing",
            ):
                service._prepare_service_state(prefix, home, repository)
            self.assertEqual(token.read_text(), "original-token")
            self.assertEqual(
                [path.name for path in authority_root.iterdir() if path.name.startswith(".")],
                [],
            )

            write_config(whatsapp=False)
            write_secret("rotated-token")
            service._prepare_service_state(prefix, home, repository)
            self.assertEqual(token.read_text(), "rotated-token")
            corrupted_status = authority_root / "zulip/STATUS.json"
            corrupted_status.chmod(0o600)
            corrupted_status.write_text('{"status":"forged"}\n', encoding="utf-8")
            corrupted_status.chmod(0o400)
            write_secret("second-must-not-publish")
            with self.assertRaisesRegex(
                service.ServiceTransactionError,
                "existing delivery projection is unrecognized",
            ):
                service._prepare_service_state(prefix, home, repository)
            self.assertEqual(token.read_text(), "rotated-token")
            self.assertEqual(
                [path.name for path in authority_root.iterdir() if path.name.startswith(".")],
                [],
            )

    def test_manifest_and_restore_cover_every_split_writer(self) -> None:
        manifest = json.loads((ROOT / "REBUILD-MANIFEST.json").read_text())
        systemd_entry = next(
            entry
            for entry in manifest["classifications"]
            if entry.get("dest") == "systemd/user"
        )
        for name in WORKER_UNITS.values():
            self.assertIn(name, systemd_entry["include"])
        self.assertIn("email-approvals/**", manifest["private_archive_exclude"])
        restore = (ROOT / "restore.sh").read_text(encoding="utf-8")
        for name in WORKER_UNITS.values():
            self.assertIn(name, restore)


if __name__ == "__main__":
    unittest.main()
