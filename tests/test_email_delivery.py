from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


QUEUE_BOUNDARY = load_module(
    "queue_boundary", ROOT / "scripts" / "queue_boundary.py"
)
EMAIL_DELIVERY = load_module(
    "email_delivery_test", ROOT / "scripts" / "email_delivery.py"
)
SEND_EMAIL = load_module(
    "send_email_test", ROOT / "workspace" / "skills" / "send-email" / "send_email.py"
)


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.workspace = self.root / "workspace"
        self.state = self.root / "state"
        self.queue = self.workspace / "data" / "email-queue"
        self.exports = self.workspace / "data" / "exports"
        self.spool = self.root / "spool"
        self.approvals = self.state / "email-approvals"
        for path in (
            self.home,
            self.workspace,
            self.state,
            self.queue,
            self.exports,
            self.spool,
            self.approvals,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
        config_directory = self.home / ".config" / "send-email"
        config_directory.mkdir(parents=True, mode=0o700)
        config_directory.chmod(0o700)
        self.smtp = config_directory / "secrets.json"
        self.smtp.write_text(
            json.dumps(
                {
                    "smtp": {
                        "host": "smtp.example.invalid",
                        "port": 465,
                        "user": "owner@example.invalid",
                        "password": "smtp-password-canary",
                        "from": "Owner <owner@example.invalid>",
                        "security": "ssl",
                        "cc": ["hidden-cc@example.invalid"],
                        "bcc": ["hidden-bcc@example.invalid"],
                        "reply_to": "hidden-reply@example.invalid",
                        "signature": "hidden signature",
                        "pgp_sign": True,
                        "pgp_key": "A" * 40,
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.smtp.chmod(0o600)
        self.pwd_patch = mock.patch.object(
            EMAIL_DELIVERY.pwd,
            "getpwuid",
            return_value=SimpleNamespace(pw_dir=os.fspath(self.home)),
        )
        self.pwd_patch.start()

    def tearDown(self) -> None:
        self.pwd_patch.stop()
        self.temporary.cleanup()

    def write_job(self, argv: list[str], *, approval_id: str = "approval-1") -> Path:
        job_id = "email-job-1"
        path = self.queue / f"{job_id}.working"
        path.write_text(
            json.dumps(
                {
                    "id": job_id,
                    "type": "email",
                    "argv": argv,
                    "status": "pending",
                    "approval_id": approval_id,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def process(self, job: Path) -> dict[str, object]:
        return EMAIL_DELIVERY.process(
            self.workspace,
            self.state,
            job,
            self.queue / "unused.result",
            self.spool,
        )

    def approve(self, approval_id: str, intent: dict[str, object]) -> None:
        policy = {
            "schema": EMAIL_DELIVERY.POLICY_SCHEMA,
            "email_policy": {
                "approved_messages": [
                    {
                        "id": approval_id,
                        "intent_sha256": intent["sha256"],
                        "recipients": intent["recipients"],
                        "signing": intent["signing"],
                        "signing_key": intent["signingKey"],
                        "account": intent["account"],
                        "sender": intent["sender"],
                    }
                ]
            },
        }
        path = self.approvals / "policy.json"
        path.write_text(json.dumps(policy) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def test_exact_approval_is_consumed_before_send_and_cannot_replay(self) -> None:
        job = self.write_job(
            [
                "send",
                "--to",
                "Recipient <recipient@example.invalid>",
                "--subject",
                "Approved subject",
                "--body",
                "Approved body",
                "--no-sign",
            ]
        )
        denied = self.process(job)
        self.assertEqual(denied["error_code"], "approval_required")
        intent = denied["intent"]
        assert isinstance(intent, dict)
        self.assertEqual(intent["recipients"], ["recipient@example.invalid"])
        self.assertFalse(intent["signing"])
        self.approve("approval-1", intent)

        def fake_send(*args, **kwargs):
            policy = json.loads((self.approvals / "policy.json").read_text())
            self.assertEqual(policy["email_policy"]["approved_messages"], [])
            self.assertEqual(kwargs["env"]["PATH"], "/usr/bin:/bin")
            self.assertEqual(kwargs["env"]["SEND_EMAIL_EXACT_QUEUE"], "1")
            return subprocess.CompletedProcess(
                args[0], 0, stdout='{"ok":true,"command":"send"}\n', stderr=""
            )

        with mock.patch.object(EMAIL_DELIVERY.subprocess, "run", side_effect=fake_send):
            delivered = self.process(job)
        self.assertTrue(delivered["ok"])
        self.assertTrue(delivered["approval_consumed"])

        replay = self.process(job)
        self.assertEqual(replay["error_code"], "approval_required")
        self.assertEqual(replay["intent"]["sha256"], intent["sha256"])

    def test_mismatched_exact_intent_does_not_consume_or_send(self) -> None:
        job = self.write_job(
            ["send", "--to", "one@example.invalid", "--body", "one", "--no-sign"]
        )
        intent = self.process(job)["intent"]
        assert isinstance(intent, dict)
        self.approve("approval-1", intent)
        job = self.write_job(
            ["send", "--to", "two@example.invalid", "--body", "one", "--no-sign"]
        )
        with mock.patch.object(EMAIL_DELIVERY.subprocess, "run") as sender:
            denied = self.process(job)
        self.assertEqual(denied["error_code"], "approval_required")
        self.assertIn("does not match", denied["message"])
        sender.assert_not_called()
        policy = json.loads((self.approvals / "policy.json").read_text())
        self.assertEqual(len(policy["email_policy"]["approved_messages"]), 1)

    def test_signed_email_requires_full_fingerprint(self) -> None:
        for argv in (
            ["send", "--to", "one@example.invalid", "--body", "x", "--sign"],
            [
                "send",
                "--to",
                "one@example.invalid",
                "--body",
                "x",
                "--sign",
                "--pgp-key",
                "DEADBEEF",
            ],
        ):
            with self.subTest(argv=argv):
                denied = self.process(self.write_job(argv))
                self.assertEqual(denied["error_code"], "approval_required")
                self.assertNotIn("intent", denied)

        approved_shape = self.process(
            self.write_job(
                [
                    "send",
                    "--to",
                    "one@example.invalid",
                    "--body",
                    "x",
                    "--sign",
                    "--pgp-key",
                    "A" * 40,
                ]
            )
        )
        self.assertTrue(approved_shape["intent"]["signing"])
        self.assertEqual(approved_shape["intent"]["signingKey"], "A" * 40)

    def test_concurrent_claim_allows_exactly_one_consumer(self) -> None:
        intent = {
            "sha256": "1" * 64,
            "recipients": ["one@example.invalid"],
            "signing": False,
            "signingKey": None,
            "account": None,
            "sender": "owner@example.invalid",
        }
        self.approve("approval-1", intent)
        policy = self.approvals / "policy.json"

        def claim() -> str:
            try:
                EMAIL_DELIVERY.consume_approval(policy, "approval-1", intent)
            except EMAIL_DELIVERY.EmailDeliveryError:
                return "denied"
            return "claimed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: claim(), range(2)))
        self.assertEqual(sorted(results), ["claimed", "denied"])

    def test_attachment_is_descriptor_snapshotted_before_send(self) -> None:
        attachment = self.exports / "evidence.txt"
        attachment.write_bytes(b"approved attachment")
        attachment.chmod(0o600)
        job = self.write_job(
            [
                "send",
                "--to",
                "one@example.invalid",
                "--body",
                "x",
                "--attach",
                "data/exports/evidence.txt",
                "--no-sign",
            ]
        )
        intent = self.process(job)["intent"]
        assert isinstance(intent, dict)
        self.approve("approval-1", intent)

        def replace_original(arguments, **_kwargs):
            attachment.write_bytes(b"attacker replacement")
            snapshot = Path(arguments[arguments.index("--attach") + 1])
            self.assertNotEqual(snapshot, attachment)
            self.assertEqual(snapshot.read_bytes(), b"approved attachment")
            self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
            return subprocess.CompletedProcess(
                arguments, 0, stdout='{"ok":true,"command":"send"}\n', stderr=""
            )

        with mock.patch.object(
            EMAIL_DELIVERY.subprocess, "run", side_effect=replace_original
        ):
            delivered = self.process(job)
        self.assertTrue(delivered["ok"])
        self.assertEqual(attachment.read_bytes(), b"attacker replacement")

    def test_exact_queue_suppresses_unapproved_config_defaults(self) -> None:
        parser = SEND_EMAIL.build_parser()
        args = parser.parse_args(
            [
                "send",
                "--to",
                "one@example.invalid",
                "--body",
                "x",
                "--no-sign",
            ]
        )
        with mock.patch.dict(
            os.environ,
            {
                "SEND_EMAIL_SECRETS_FILE": os.fspath(self.smtp),
                "SEND_EMAIL_EXACT_QUEUE": "1",
            },
            clear=True,
        ):
            config = SEND_EMAIL.load_config(args)
        self.assertEqual(config.host, "smtp.example.invalid")
        self.assertEqual(config.password, "smtp-password-canary")
        self.assertEqual(config.sender, "Owner <owner@example.invalid>")
        self.assertEqual(config.cc, [])
        self.assertEqual(config.bcc, [])
        self.assertIsNone(config.reply_to)
        self.assertIsNone(config.signature)
        self.assertFalse(config.reply_to_self)
        self.assertFalse(config.bcc_self)
        self.assertFalse(config.pgp_sign)
        self.assertIsNone(config.pgp_key)

    def test_openclaw_launcher_cannot_bypass_queue_via_workspace_environment(self) -> None:
        launcher = (
            ROOT / "workspace" / "skills" / "send-email" / "run_send_email.sh"
        )
        base_environment = {
            "HOME": os.fspath(self.home),
            "PATH": "/usr/bin:/bin",
            "SMTP_PASSWORD": "ambient-smtp-canary",
            "SMTP_HOST": "attacker.invalid",
        }
        for workspace_value in (None, os.fspath(self.root / "attacker-workspace")):
            environment = dict(base_environment)
            if workspace_value is not None:
                environment["OPENCLAW_WORKSPACE"] = workspace_value
            with self.subTest(workspace=workspace_value):
                result = subprocess.run(
                    [
                        "/usr/bin/bash",
                        "-p",
                        os.fspath(launcher),
                        "send",
                        "--to",
                        "one@example.invalid",
                        "--body",
                        "x",
                        "--no-sign",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=environment,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn('"error_code":"approval_required"', result.stdout)
                self.assertNotIn("ambient-smtp-canary", result.stdout + result.stderr)
                self.assertNotIn("no_host", result.stdout + result.stderr)

        signed_preview = subprocess.run(
            [
                "/usr/bin/bash",
                "-p",
                os.fspath(launcher),
                "send",
                "--to",
                "one@example.invalid",
                "--body",
                "x",
                "--sign",
                "--pgp-key",
                "A" * 40,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=base_environment,
        )
        self.assertEqual(signed_preview.returncode, 2)
        self.assertIn("approval_required", signed_preview.stdout)

    def test_unsigned_local_preview_scrubs_ambient_smtp_defaults(self) -> None:
        launcher = (
            ROOT / "workspace" / "skills" / "send-email" / "run_send_email.sh"
        )
        result = subprocess.run(
            [
                "/usr/bin/bash",
                "-p",
                os.fspath(launcher),
                "send",
                "--to",
                "one@example.invalid",
                "--from",
                "owner@example.invalid",
                "--body",
                "x",
                "--no-sign",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                "HOME": os.fspath(self.home),
                "PATH": "/usr/bin:/bin",
                "OPENCLAW_WORKSPACE": "/attacker-controlled",
                "SMTP_PASSWORD": "ambient-smtp-canary",
                "SMTP_BCC": "hidden@example.invalid",
                "SMTP_SIGNATURE": "hidden signature",
                "SMTP_PGP_SIGN": "true",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["recipients"], ["one@example.invalid"])
        self.assertFalse(payload["signed"])
        self.assertNotIn("ambient-smtp-canary", result.stdout + result.stderr)
        self.assertNotIn("hidden@example.invalid", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
