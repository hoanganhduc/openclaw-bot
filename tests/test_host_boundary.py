"""Host/sandbox boundary regressions for the OpenClaw component."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(
        f"openclaw_boundary_{name}", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # Dataclass and other runtime type helpers expect an importable module.
    import sys

    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class FileDeliveryBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.delivery = load_script("file_delivery")

    @staticmethod
    def _policy(target: str = "approved-chat") -> dict[str, object]:
        return {
            "schema": "openclaw.file-delivery-policy/v1",
            "delivery_policy": {
                "allowed_targets": {
                    "telegram": [target],
                    "zulip": [],
                    "googlechat": [],
                    "whatsapp": [],
                    "zalo": [],
                }
            },
        }

    def _fixture(
        self,
        root: Path,
        media: str = "data/research/zotero/staging/paper.pdf",
    ) -> tuple[Path, Path, Path, Path]:
        home = root / "account"
        workspace = home / "workspace"
        export = workspace / media
        state = home / ".openclaw"
        queue = workspace / "data/send-queue"
        for directory in (home, workspace, export.parent, state, queue):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        export.write_bytes(b"descriptor-bound-original")
        export.chmod(0o600)
        policy = state / "file-delivery-policy.json"
        policy.write_text(json.dumps(self._policy()) + "\n", encoding="utf-8")
        policy.chmod(0o600)
        job = queue / "send-1.working"
        job.write_text(
            json.dumps(
                {
                    "schema": "openclaw.send-queue-job/v1",
                    "id": "send-1",
                    "channel": "telegram",
                    "target": "approved-chat",
                    "media": f"/workspace/{media}",
                    "caption": "fixture",
                    "status": "pending",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        job.chmod(0o600)
        return home, workspace, state, job

    def test_normal_home_spool_and_descriptor_snapshot_survive_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            home, workspace, state, job = self._fixture(root)
            source = workspace / "data/research/zotero/staging/paper.pdf"
            observed: list[bytes] = []

            def sender(**kwargs: object) -> None:
                snapshot = kwargs["snapshot"]
                source.unlink()
                source.write_bytes(b"attacker-replacement")
                observed.append(os.pread(snapshot.descriptor, snapshot.size, 0))

            with mock.patch.object(
                self.delivery.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(home)),
            ):
                result = self.delivery.process_job(
                    workspace=workspace,
                    policy_path=state / "file-delivery-policy.json",
                    expected_channel="telegram",
                    job_path=job,
                    telegram_credential=state / "telegram-token",
                    sender=sender,
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(observed, [b"descriptor-bound-original"])
            self.assertEqual(source.read_bytes(), b"attacker-replacement")

    def test_vnu_document_snapshot_survives_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            media = "data/vnu_eoffice/documents/decision.pdf"
            home, workspace, state, job = self._fixture(root, media)
            source = workspace / media
            observed: list[bytes] = []

            def sender(**kwargs: object) -> None:
                snapshot = kwargs["snapshot"]
                source.unlink()
                source.write_bytes(b"attacker-vnu-replacement")
                observed.append(os.pread(snapshot.descriptor, snapshot.size, 0))

            with mock.patch.object(
                self.delivery.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(home)),
            ):
                result = self.delivery.process_job(
                    workspace=workspace,
                    policy_path=state / "file-delivery-policy.json",
                    expected_channel="telegram",
                    job_path=job,
                    telegram_credential=state / "telegram-token",
                    sender=sender,
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(observed, [b"descriptor-bound-original"])

    def test_vnu_document_delivery_still_requires_exact_target_policy(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            home, workspace, state, job = self._fixture(
                root, "data/vnu_eoffice/documents/decision.pdf"
            )
            payload = json.loads(job.read_text(encoding="utf-8"))
            payload["target"] = "not-approved"
            job.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            called = False

            def sender(**_kwargs: object) -> None:
                nonlocal called
                called = True

            with mock.patch.object(
                self.delivery.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(home)),
            ):
                with self.assertRaisesRegex(
                    self.delivery.DeliveryError, "target is not authorized"
                ):
                    self.delivery.process_job(
                        workspace=workspace,
                        policy_path=state / "file-delivery-policy.json",
                        expected_channel="telegram",
                        job_path=job,
                        telegram_credential=state / "telegram-token",
                        sender=sender,
                    )
            self.assertFalse(called)

    def test_policy_replacement_during_read_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir(mode=0o700)
            policy = state / "file-delivery-policy.json"
            replacement = state / "replacement.json"
            policy.write_text(json.dumps(self._policy()) + "\n", encoding="utf-8")
            replacement.write_text(
                json.dumps(self._policy("attacker-chat")) + "\n",
                encoding="utf-8",
            )
            policy.chmod(0o600)
            replacement.chmod(0o600)
            original_read = self.delivery.os.read
            replaced = False

            def racing_read(descriptor: int, count: int) -> bytes:
                nonlocal replaced
                payload = original_read(descriptor, count)
                if not replaced:
                    replaced = True
                    os.replace(replacement, policy)
                return payload

            with mock.patch.object(self.delivery.os, "read", side_effect=racing_read):
                with self.assertRaisesRegex(
                    self.delivery.DeliveryError, "changed while reading"
                ):
                    self.delivery.load_policy(
                        policy, channel="telegram", target="approved-chat"
                    )

    def test_delivery_staging_is_not_approved_provenance(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            workspace = Path(temporary) / "workspace"
            staging = workspace / "data/delivery-staging"
            staging.mkdir(parents=True, mode=0o700)
            artifact = staging / "private.txt"
            artifact.write_text("must-not-send", encoding="utf-8")
            artifact.chmod(0o600)
            with self.assertRaisesRegex(
                self.delivery.DeliveryError, "outside approved export roots"
            ):
                self.delivery.snapshot_export(workspace, artifact)

    def test_spool_accepts_trusted_system_ancestors_and_rejects_unsafe_ones(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            home = root / "account"
            home.mkdir(mode=0o700)
            account = SimpleNamespace(pw_dir=str(home))
            with mock.patch.object(self.delivery.pwd, "getpwuid", return_value=account):
                descriptor = self.delivery._ensure_private_directory(
                    home / ".local/state/openclaw-bot/delivery-spool"
                )
                os.close(descriptor)
                home.chmod(0o770)
                try:
                    with self.assertRaisesRegex(
                        self.delivery.DeliveryError, "not owner-private"
                    ):
                        self.delivery._ensure_private_directory(
                            home / ".local/state/openclaw-bot/other-spool"
                        )
                finally:
                    home.chmod(0o700)

            symlink_home = root / "symlink-account"
            symlink_home.mkdir(mode=0o700)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            (symlink_home / ".local").symlink_to(outside, target_is_directory=True)
            with mock.patch.object(
                self.delivery.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(symlink_home)),
            ):
                with self.assertRaises(OSError):
                    self.delivery._ensure_private_directory(
                        symlink_home / ".local/state/openclaw-bot/delivery-spool"
                    )

    def test_delivery_accepts_only_expected_tmpfs_home_ancestors(self) -> None:
        home = Path("/home/example-user")
        euid = 1001
        directory = stat.S_IFDIR
        self.assertTrue(
            self.delivery._ancestor_is_controlled(
                Path("/"),
                SimpleNamespace(st_uid=65534, st_mode=directory | 0o755),
                account_home=home,
            )
        )
        self.assertTrue(
            self.delivery._ancestor_is_controlled(
                Path("/home"),
                SimpleNamespace(st_uid=euid, st_mode=directory | 0o1777),
                account_home=home,
            )
        )
        self.assertTrue(
            self.delivery._ancestor_is_controlled(
                Path("/home"),
                SimpleNamespace(st_uid=0, st_mode=directory | 0o755),
                account_home=home,
            )
        )
        self.assertFalse(
            self.delivery._ancestor_is_controlled(
                Path("/home"),
                SimpleNamespace(st_uid=euid, st_mode=directory | 0o0777),
                account_home=home,
            )
        )
        self.assertFalse(
            self.delivery._ancestor_is_controlled(
                home,
                SimpleNamespace(st_uid=euid, st_mode=directory | 0o0770),
                account_home=home,
            )
        )

    def test_telegram_uses_form_string_for_untrusted_text(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            payload = Path(temporary) / "payload"
            payload.write_bytes(b"document")
            payload.chmod(0o600)
            descriptor = os.open(payload, os.O_RDONLY)
            snapshot = SimpleNamespace(
                descriptor=descriptor,
                display_name="document.pdf",
            )
            completed = SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
            try:
                with mock.patch.object(
                    self.delivery, "_telegram_token", return_value="token-canary"
                ), mock.patch.object(
                    self.delivery.subprocess, "run", return_value=completed
                ) as runner:
                    self.delivery._send_telegram(
                        credential_path=Path(temporary) / "telegram-token",
                        target="@target;type=text/plain",
                        caption="@caption-file;type=text/plain",
                        snapshot=snapshot,
                    )
            finally:
                os.close(descriptor)
            arguments = runner.call_args.args[0]
            form_values = [
                arguments[index + 1]
                for index, value in enumerate(arguments[:-1])
                if value == "--form"
            ]
            form_string_values = [
                arguments[index + 1]
                for index, value in enumerate(arguments[:-1])
                if value == "--form-string"
            ]
            self.assertEqual(len(form_values), 1)
            self.assertTrue(form_values[0].startswith("document=@/proc/self/fd/"))
            self.assertEqual(
                form_string_values,
                [
                    "chat_id=@target;type=text/plain",
                    "caption=@caption-file;type=text/plain",
                ],
            )

    def test_telegram_prefixed_target_is_stripped_for_bot_api(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            payload = Path(temporary) / "payload"
            payload.write_bytes(b"document")
            payload.chmod(0o600)
            descriptor = os.open(payload, os.O_RDONLY)
            snapshot = SimpleNamespace(
                descriptor=descriptor,
                display_name="document.pdf",
            )
            completed = SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
            try:
                with mock.patch.object(
                    self.delivery, "_telegram_token", return_value="token-canary"
                ), mock.patch.object(
                    self.delivery.subprocess, "run", return_value=completed
                ) as runner:
                    self.delivery._send_telegram(
                        credential_path=Path(temporary) / "telegram-token",
                        target="telegram:123456",
                        caption="caption",
                        snapshot=snapshot,
                    )
            finally:
                os.close(descriptor)
            arguments = runner.call_args.args[0]
            form_string_values = [
                arguments[index + 1]
                for index, value in enumerate(arguments[:-1])
                if value == "--form-string"
            ]
            self.assertIn("chat_id=123456", form_string_values)
            self.assertNotIn("chat_id=telegram:123456", form_string_values)

    def test_worker_channel_binding_rejects_cross_channel_queue_jobs(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            home, workspace, state, job = self._fixture(root)
            called = False

            def sender(**_kwargs: object) -> None:
                nonlocal called
                called = True

            with mock.patch.object(
                self.delivery.pwd,
                "getpwuid",
                return_value=SimpleNamespace(pw_dir=str(home)),
            ):
                with self.assertRaisesRegex(
                    self.delivery.DeliveryError,
                    "queue job channel does not match this worker",
                ):
                    self.delivery.process_job(
                        workspace=workspace,
                        policy_path=state / "file-delivery-policy.json",
                        expected_channel="zulip",
                        job_path=job,
                        channel_state=state / "zulip",
                        sender=sender,
                    )
            self.assertFalse(called)

    def test_nonconfigured_or_cross_channel_projection_fails_before_send(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            projection = root / "zulip"
            projection.mkdir(mode=0o700)
            status = projection / "STATUS.json"
            status.write_text(
                json.dumps(
                    {
                        "schema": "openclaw.delivery-projection/v1",
                        "channel": "zulip",
                        "status": "NOT_CONFIGURED",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            status.chmod(0o400)
            with self.assertRaisesRegex(
                self.delivery.DeliveryError,
                "channel projection is not configured",
            ):
                self.delivery._validate_channel_projection(
                    projection,
                    channel="zulip",
                )
            status.chmod(0o600)
            status.write_text(
                json.dumps(
                    {
                        "schema": "openclaw.delivery-projection/v1",
                        "channel": "zalo",
                        "status": "CONFIGURED",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            status.chmod(0o400)
            with self.assertRaisesRegex(
                self.delivery.DeliveryError,
                "channel projection is not configured",
            ):
                self.delivery._validate_channel_projection(
                    projection,
                    channel="zulip",
                )

    def test_telegram_reads_only_the_exact_projected_credential(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            credential = root / "telegram-token"
            credential.write_text("exact-telegram-token", encoding="utf-8")
            credential.chmod(0o600)
            broad = root / "secrets.json"
            broad.write_text(
                '{"TELEGRAM_BOT_TOKEN":"broad-secret-canary",'
                '"OPENAI_API_KEY":"provider-canary"}\n',
                encoding="utf-8",
            )
            broad.chmod(0o600)
            self.assertEqual(
                self.delivery._telegram_token(credential), "exact-telegram-token"
            )


class InstalledBoundaryContractTests(unittest.TestCase):
    def test_units_execute_only_attested_host_artifacts(self) -> None:
        for unit in sorted((ROOT / "systemd/user").glob("*.service")):
            text = unit.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("ExecStart"):
                    if line.startswith("ExecStart=/usr/bin/Xvfb"):
                        continue
                    self.assertIn("{{ OPENCLAW_LIBEXEC }}/host_exec.py", line, unit)
                    self.assertNotIn("/workspace/", line, unit)
                    self.assertNotIn("{{ OPENCLAW_WORKSPACE }}/", line, unit)
            if unit.name != "xvfb-99.service":
                self.assertIn("Environment=PATH=/usr/bin:/bin", text, unit)
            if unit.name in {"openclaw-gateway.service", "send-queue-worker.service"}:
                self.assertIn("RuntimeDirectoryMode=0700", text, unit)
                self.assertRegex(text, r"Environment=TMPDIR=%t/openclaw-bot-[a-z]+")
                self.assertNotIn("Environment=TMPDIR=/tmp", text, unit)

    def test_restart_and_operator_commands_resolve_one_generation(self) -> None:
        units = [
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / "systemd/user").glob("*.service"))
            if path.name != "xvfb-99.service"
        ]
        self.assertTrue(units)
        self.assertTrue(
            all("Environment=OPENCLAW_LIBEXEC={{ OPENCLAW_LIBEXEC }}" in text for text in units)
        )
        helper = (ROOT / "scripts/run_host_command.py").read_text(encoding="utf-8")
        self.assertIn('choices=("health", "cron-export")', helper)
        self.assertIn('generation / "host_exec.py"', helper)
        cli = (ROOT / "scripts/openclaw_host_cli.py").read_text(encoding="utf-8")
        self.assertIn('["health", "--json", "--timeout"', cli)
        self.assertIn('["cron", "list", "--all", "--json"]', cli)

    def test_launcher_checks_every_generation_parent(self) -> None:
        launcher = load_script("host_exec")
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            home = Path(temporary) / "account"
            generation = (
                home
                / ".local/libexec/openclaw-bot/generations"
                / ("a" * 64)
            )
            generation.mkdir(parents=True, mode=0o700)
            for path in (
                home,
                home / ".local",
                home / ".local/libexec",
                home / ".local/libexec/openclaw-bot",
                generation.parent,
                generation,
            ):
                path.chmod(0o700)
            account = SimpleNamespace(pw_dir=str(home))
            with mock.patch.object(launcher.pwd, "getpwuid", return_value=account):
                self.assertEqual(launcher._validate_generation_path(generation), generation)
                home.chmod(0o770)
                try:
                    with self.assertRaisesRegex(
                        launcher.HostExecError, "parent is not owner-controlled"
                    ):
                        launcher._validate_generation_path(generation)
                finally:
                    home.chmod(0o700)

    def test_launcher_accepts_only_expected_tmpfs_home_ancestors(self) -> None:
        launcher = load_script("host_exec")
        home = Path("/home/example-user")
        euid = 1001
        directory = stat.S_IFDIR
        self.assertTrue(
            launcher._parent_is_controlled(
                Path("/"),
                SimpleNamespace(st_uid=65534, st_mode=directory | 0o755),
                home=home,
                euid=euid,
            )
        )
        self.assertTrue(
            launcher._parent_is_controlled(
                Path("/home"),
                SimpleNamespace(st_uid=euid, st_mode=directory | 0o1777),
                home=home,
                euid=euid,
            )
        )
        self.assertFalse(
            launcher._parent_is_controlled(
                Path("/home"),
                SimpleNamespace(st_uid=euid, st_mode=directory | 0o0777),
                home=home,
                euid=euid,
            )
        )
        self.assertFalse(
            launcher._parent_is_controlled(
                home,
                SimpleNamespace(st_uid=euid, st_mode=directory | 0o0770),
                home=home,
                euid=euid,
            )
        )


class SealedRuntimeLayoutTests(unittest.TestCase):
    """A coding-system restore links ~/.npm-global into sealed generations."""

    VERSION = "2026.7.1-2"
    NODE_GENERATION = "sha256-amd64-" + "1" * 64
    CLOSURE = "sha256-amd64-" + "2" * 64 + "-" + "3" * 64

    def setUp(self) -> None:
        self.service = load_script("service_transaction")
        self.cli = load_script("openclaw_host_cli")
        temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(self._unseal)
        self.home = self.root / "account"
        self.coding = self.home / ".local/share/coding-system"
        self.node = self.coding / "node-generations" / self.NODE_GENERATION / "bin/node"
        self.modules = self.coding / "npm-closures" / self.CLOSURE / "node_modules"
        self.repository = self.root / "repository"
        self.repository.mkdir(mode=0o700)
        self.repository.chmod(0o700)
        manifest = self.repository / "REBUILD-MANIFEST.json"
        manifest.write_text(
            json.dumps({"openclaw": {"observed_version": self.VERSION}}),
            encoding="utf-8",
        )
        manifest.chmod(0o644)

    def _unseal(self) -> None:
        for directory in [self.root, *self.root.rglob("*")]:
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o755)

    @staticmethod
    def _directory(path: Path, mode: int = 0o755) -> None:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)

    @staticmethod
    def _file(path: Path, payload: str, mode: int = 0o444) -> None:
        path.write_text(payload, encoding="utf-8")
        path.chmod(mode)

    def _build_sealed(self) -> None:
        for directory in (
            self.home,
            self.home / ".local",
            self.home / ".local/share",
            self.coding,
            self.coding / "node-generations",
            self.coding / "npm-closures",
            self.home / ".npm-global",
            self.home / ".npm-global/bin",
            self.home / ".npm-global/lib",
            self.home / ".npm-global/lib/node_modules",
        ):
            self._directory(directory)
        for directory in (
            self.node.parent,
            self.modules / "openclaw/dist",
            self.modules / "dependency",
        ):
            self._directory(directory)
        self._file(self.node, "#!/bin/sh\n", 0o555)
        self._file(
            self.modules / "openclaw/package.json",
            json.dumps({"name": "openclaw", "version": self.VERSION}),
        )
        self._file(self.modules / "openclaw/dist/index.js", "export {};\n")
        self._file(self.modules / "dependency/index.js", "module.exports = 1;\n")
        (self.home / ".npm-global/bin/node").symlink_to(self.node)
        (self.home / ".npm-global/lib/node_modules/openclaw").symlink_to(
            self.modules / "openclaw"
        )
        # The builder seals every generation as read-only directories.
        for generation in (self.node.parents[1], self.modules.parent):
            for directory in sorted(
                (path for path in [generation, *generation.rglob("*")] if path.is_dir()),
                reverse=True,
            ):
                directory.chmod(0o555)

    def _modes(self) -> dict[str, int]:
        return {
            os.fspath(path): stat.S_IMODE(path.lstat().st_mode)
            for path in self.coding.rglob("*")
        }

    def test_install_attests_sealed_generations_that_the_host_cli_accepts(self) -> None:
        self._build_sealed()
        before = self._modes()
        runtime = self.service._collect_external_runtime(
            self.repository, self.home, harden=True
        )
        self.assertEqual(before, self._modes())
        package_root = self.modules / "openclaw"
        self.assertEqual(runtime["node"]["path"], os.fspath(self.node))
        self.assertEqual(
            runtime["entry"]["path"], os.fspath(package_root / "dist/index.js")
        )
        self.assertEqual(
            runtime["package"]["path"], os.fspath(package_root / "package.json")
        )

        name = "a" * 64
        generation = self.home / ".local/libexec/openclaw-bot/generations" / name
        for directory in (
            self.home / ".local/libexec",
            self.home / ".local/libexec/openclaw-bot",
            generation.parent,
            generation,
        ):
            self._directory(directory, 0o700)
        self._file(
            generation / "MANIFEST.json",
            json.dumps(
                {
                    "schema": "openclaw.host-runtime/v1",
                    "generation": name,
                    "artifacts": [],
                    "externalRuntime": runtime,
                }
            ),
            0o400,
        )
        account = SimpleNamespace(pw_dir=os.fspath(self.home))
        with mock.patch.object(self.cli.pwd, "getpwuid", return_value=account):
            node, entry, dist, version = self.cli._attested_runtime(generation)
        for descriptor in (node, entry, dist):
            os.close(descriptor)
        self.assertEqual(version, self.VERSION)

    def test_sealed_links_must_stay_inside_matching_generations(self) -> None:
        self._build_sealed()
        link = self.home / ".npm-global/bin/node"
        other = self.coding / "node-generations" / ("sha256-arm64-" + "1" * 64)
        self._directory(other / "bin")
        self._file(other / "bin/node", "#!/bin/sh\n", 0o555)
        link.unlink()
        link.symlink_to(other / "bin/node")
        with self.assertRaisesRegex(
            self.service.ServiceTransactionError, "leave the sealed"
        ):
            self.service._collect_external_runtime(
                self.repository, self.home, harden=True
            )
        link.unlink()
        link.symlink_to(self.node)
        outside = self.home / "elsewhere/node_modules/openclaw"
        self._directory(outside)
        package_link = self.home / ".npm-global/lib/node_modules/openclaw"
        package_link.unlink()
        package_link.symlink_to(outside)
        with self.assertRaisesRegex(
            self.service.ServiceTransactionError, "leave the sealed"
        ):
            self.service._collect_external_runtime(
                self.repository, self.home, harden=True
            )

    def test_sealed_generations_are_verified_and_never_repaired(self) -> None:
        self._build_sealed()
        dependency = self.modules / "dependency/index.js"
        dependency.chmod(0o664)
        with self.assertRaisesRegex(
            self.service.ServiceTransactionError, "sealed OpenClaw runtime path is writable"
        ):
            self.service._collect_external_runtime(
                self.repository, self.home, harden=True
            )
        self.assertEqual(stat.S_IMODE(dependency.stat().st_mode), 0o664)

    @unittest.skipUnless(Path("/usr/bin/node").is_file(), "system Node is required")
    def test_legacy_npm_global_tree_is_still_attested_and_repaired(self) -> None:
        package_root = self.home / ".npm-global/lib/node_modules/openclaw"
        for directory in (
            self.home,
            self.home / ".npm-global",
            self.home / ".npm-global/lib",
            self.home / ".npm-global/lib/node_modules",
            package_root,
            package_root / "dist",
        ):
            self._directory(directory)
        self._file(
            package_root / "package.json",
            json.dumps({"name": "openclaw", "version": self.VERSION}),
            0o664,
        )
        self._file(package_root / "dist/index.js", "export {};\n", 0o644)
        runtime = self.service._collect_external_runtime(
            self.repository, self.home, harden=True
        )
        self.assertEqual(runtime["node"]["path"], "/usr/bin/node")
        self.assertEqual(
            runtime["package"]["path"], os.fspath(package_root / "package.json")
        )
        self.assertEqual(
            stat.S_IMODE((package_root / "package.json").stat().st_mode), 0o644
        )

    def test_host_cli_accepts_only_the_two_runtime_layouts(self) -> None:
        home = Path("/home/example-user")
        coding = "/home/example-user/.local/share/coding-system"
        closure = f"{coding}/npm-closures/{self.CLOSURE}/node_modules/openclaw"
        node = f"{coding}/node-generations/{self.NODE_GENERATION}/bin/node"

        def runtime(node_path: str, package_path: str) -> dict[str, object]:
            return {
                "version": self.VERSION,
                "node": {"path": node_path},
                "entry": {"path": package_path.replace("package.json", "dist/index.js")},
                "package": {"path": package_path},
            }

        legacy = self.cli._expected_runtime_paths(
            runtime(
                "/usr/bin/node",
                "/home/example-user/.npm-global/lib/node_modules/openclaw/package.json",
            ),
            home,
        )
        self.assertEqual(legacy["node"], Path("/usr/bin/node"))
        self.assertEqual(
            legacy["entry"],
            home / ".npm-global/lib/node_modules/openclaw/dist/index.js",
        )
        sealed = self.cli._expected_runtime_paths(
            runtime(node, f"{closure}/package.json"), home
        )
        self.assertEqual(sealed["node"], Path(node))
        self.assertEqual(sealed["entry"], Path(f"{closure}/dist/index.js"))
        for node_path, package_path in (
            (node.replace("sha256-amd64", "sha256-arm64"), f"{closure}/package.json"),
            ("/opt/node/bin/node", f"{closure}/package.json"),
            (node, "/home/example-user/.npm-global/lib/node_modules/openclaw/package.json"),
            (node, f"{closure.replace('/node_modules/openclaw', '/node_modules/other')}/package.json"),
        ):
            with self.assertRaisesRegex(
                self.cli.RuntimeError_, "attestation path is invalid"
            ):
                self.cli._expected_runtime_paths(runtime(node_path, package_path), home)

    def test_host_cli_accepts_the_delivery_sandbox_ancestors(self) -> None:
        home = Path("/home/example-user")
        euid = os.geteuid()
        directory = stat.S_IFDIR
        accepted = (
            (Path("/"), 65534, 0o755),
            (Path("/home"), euid, 0o1777),
            (home, euid, 0o700),
        )
        rejected = (
            (Path("/"), 65534, 0o777),
            (Path("/usr"), 65534, 0o755),
            (Path("/home"), euid, 0o1775),
            (Path("/home"), euid, 0o0777),
            (Path("/home"), 65534, 0o1777),
            (home / ".local", euid, 0o1777),
            (home, euid, 0o770),
        )
        for path, uid, mode in accepted:
            information = SimpleNamespace(st_uid=uid, st_mode=directory | mode)
            self.assertTrue(
                self.cli._ancestor_is_controlled(path, information, home=home),
                (path, uid, oct(mode)),
            )
        for path, uid, mode in rejected:
            information = SimpleNamespace(st_uid=uid, st_mode=directory | mode)
            self.assertFalse(
                self.cli._ancestor_is_controlled(path, information, home=home),
                (path, uid, oct(mode)),
            )


if __name__ == "__main__":
    unittest.main()
