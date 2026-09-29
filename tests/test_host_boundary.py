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
        home = Path("/home/ubuntu")
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
        home = Path("/home/ubuntu")
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


if __name__ == "__main__":
    unittest.main()
