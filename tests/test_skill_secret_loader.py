from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / "workspace/skills/_load_skill_secrets.py"


def load_module():
    specification = importlib.util.spec_from_file_location("skill_secret_loader_test", LOADER)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class SkillSecretLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_strict_env_parser_does_not_evaluate_shell(self) -> None:
        parsed = self.module.parse_secret_env_text("AXLE_API_KEY=$(touch /tmp/nope)\n")
        self.assertEqual(parsed["AXLE_API_KEY"], "$(touch /tmp/nope)")
        for payload in (
            " AXLE_API_KEY=value\n",
            "AXLE_API_KEY=value \n",
            "AXLE_API_KEY=\n",
            "AXLE_API_KEY=value\nAXLE_API_KEY=again\n",
            "bad=value\n",
        ):
            with self.assertRaises(self.module.SkillSecretError):
                self.module.parse_secret_env_text(payload)

    def test_compiled_projection_rejects_cross_skill_keys(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir(mode=0o700)
            path = workspace / "axle.env"
            path.write_text("AXLE_API_KEY=selected\nOPENCLAW_S2_API_KEY=wrong-scope\n")
            path.chmod(0o600)
            profile = self.module.Profile(
                "AAS_AXLE_SECRETS_FILE",
                "workspace:axle.env",
                frozenset({"AXLE_API_KEY"}),
                "axiom-axle-mcp/axiom_axle_mcp.py",
            )
            with self.assertRaises(self.module.SkillSecretError):
                self.module._load_profile(profile, workspace, Path(temporary))

    def test_json_projection_is_private_bounded_and_exact(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir(mode=0o700)
            path = workspace / "zotero.json"
            path.write_text(json.dumps({"ZOTERO_API_KEY": "selected"}))
            path.chmod(0o600)
            profile = self.module.Profile(
                "AAS_ZOTERO_SKILL_SECRETS_FILE",
                "workspace:zotero.json",
                frozenset({"ZOTERO_API_KEY"}),
                "zotero/zot.py",
            )
            value = self.module._load_profile(profile, workspace, Path(temporary))
            self.assertEqual(value, {"ZOTERO_API_KEY": "selected"})
            path.chmod(0o644)
            with self.assertRaises(self.module.SkillSecretError):
                self.module._load_profile(profile, workspace, Path(temporary))

    def test_calibre_projection_is_json_only_and_rejects_delivery_credentials(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir(mode=0o700)
            path = workspace / "calibre.json"
            path.write_text(
                json.dumps(
                    {
                        "GDRIVE_CREDENTIALS": {"type": "service_account"},
                        "CALIBRE_GDRIVE_FOLDER_ID": "folder-id",
                    }
                )
            )
            path.chmod(0o600)
            profile = self.module.Profile(
                "AAS_CALIBRE_SECRETS_FILE",
                "workspace:calibre.json",
                frozenset({"GDRIVE_CREDENTIALS", "CALIBRE_GDRIVE_FOLDER_ID"}),
                "calibre/cal.py",
                json_only=True,
            )
            loaded = self.module._load_profile(profile, workspace, Path(temporary))
            self.assertEqual(loaded["CALIBRE_GDRIVE_FOLDER_ID"], "folder-id")
            self.assertEqual(
                json.loads(loaded["GDRIVE_CREDENTIALS"]),
                {"type": "service_account"},
            )

            path.write_text("GDRIVE_CREDENTIALS=not-json\n")
            with self.assertRaises(self.module.SkillSecretError):
                self.module._load_profile(profile, workspace, Path(temporary))

            path.write_text(
                json.dumps(
                    {
                        "GDRIVE_CREDENTIALS": "selected",
                        "TELEGRAM_BOT_TOKEN": "wrong-authority",
                    }
                )
            )
            with self.assertRaises(self.module.SkillSecretError):
                self.module._load_profile(profile, workspace, Path(temporary))

    def test_symlinked_secret_authority_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real.env"
            target.write_text("AXLE_API_KEY=selected\n")
            target.chmod(0o600)
            link = root / "link.env"
            link.symlink_to(target)
            with self.assertRaises(self.module.SkillSecretError):
                self.module.read_protected_secret_env(str(link))

    def test_arbitrary_selector_and_python_script_options_are_not_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "arbitrary-script-ran"
            child = root / "child.py"
            child.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
            environment = dict(os.environ)
            environment.update(
                {
                    "PYTHONPATH": str(root),
                    "AAS_AXLE_SECRETS_FILE": str(root / "attacker.env"),
                    "AAS_SECRETS_FILE": "/shared/authority.json",
                    "OPENCLAW_SECRETS_FILE": "/legacy/authority.json",
                }
            )
            result = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-S",
                    "-B",
                    str(LOADER),
                    "--secret-file-env",
                    "AAS_AXLE_SECRETS_FILE",
                    "--python-script",
                    str(child),
                ],
                text=True,
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertNotIn("attacker.env", result.stdout + result.stderr)

    def test_safe_child_environment_excludes_ambient_secrets_and_pointers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir(mode=0o700)
            with mock.patch.dict(
                os.environ,
                {
                    "AAS_CALIBRE_SECRETS_FILE": str(root / "attacker.json"),
                    "AAS_FILE_DELIVERY_SECRETS_FILE": str(root / "delivery.json"),
                    "REMOTE_BRIDGE_SECRETS_FILE": str(root / "bridge.json"),
                    "TELEGRAM_BOT_TOKEN": "ambient-telegram",
                    "ZULIP_API_KEY": "ambient-zulip",
                    "AWS_SECRET_ACCESS_KEY": "ambient-aws",
                    "PYTHONPATH": str(root),
                },
                clear=True,
            ):
                projected = self.module._safe_child_environment(
                    {
                        "GDRIVE_CREDENTIALS": "selected-drive-credentials",
                        "CALIBRE_GDRIVE_FOLDER_ID": "selected-folder",
                    },
                    workspace,
                    root,
                )
            self.assertEqual(projected["GDRIVE_CREDENTIALS"], "selected-drive-credentials")
            self.assertEqual(projected["CALIBRE_GDRIVE_FOLDER_ID"], "selected-folder")
            for name in (
                "AAS_CALIBRE_SECRETS_FILE",
                "AAS_FILE_DELIVERY_SECRETS_FILE",
                "REMOTE_BRIDGE_SECRETS_FILE",
                "TELEGRAM_BOT_TOKEN",
                "ZULIP_API_KEY",
                "AWS_SECRET_ACCESS_KEY",
                "PYTHONPATH",
            ):
                self.assertNotIn(name, projected)

    def test_loader_has_one_canonical_copy(self) -> None:
        copies = sorted((ROOT / "workspace/skills").glob("*/_load_skill_secrets.py"))
        self.assertEqual(copies, [])
        self.assertTrue(LOADER.is_file())

    def test_wrappers_use_isolated_loader_and_compiled_profiles(self) -> None:
        contracts = {
            "axiom-axle-mcp/run_axiom_axle_mcp.sh": "axiom-axle-mcp",
            "lean-explore-cli/run_lean_explore.sh": "lean-explore-cli",
            "lean-explore-mcp/run_lean_explore_mcp.sh": "lean-explore-mcp",
            "research-digest-wrapper/run_research_digest.sh": "research-digest",
            "submission-venue-selector/run_submission_venue_selector.sh": "submission-venue-selector",
            "zotero/run_zot.sh": "zotero",
            "calibre/run_cal.sh": "calibre",
        }
        base = ROOT / "workspace/skills"
        for relative, profile in contracts.items():
            text = (base / relative).read_text()
            self.assertIn("/usr/bin/python3", text, relative)
            self.assertIn("-I -S -B", text, relative)
            self.assertIn(f"--profile {profile}", text, relative)
            self.assertNotIn("--secret-file-env", text, relative)
            self.assertNotIn("--python-script", text, relative)
            self.assertNotIn("--allow-selector", text, relative)
            self.assertNotIn("${AAS_SECRETS_FILE", text, relative)
            self.assertNotIn("${OPENCLAW_SECRETS_FILE", text, relative)

    def test_lean_explore_mcp_config_keeps_api_key_out_of_process_arguments(self) -> None:
        canary = "synthetic-leanexplore-key-never-in-argv"
        helper = ROOT / "workspace/skills/lean-explore-mcp/lean_explore_mcp.py"
        result = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                str(helper),
                "config-snippet",
                "--backend",
                "api",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                "HOME": str(Path.home()),
                "PATH": "/usr/bin:/bin",
                "LEANEXPLORE_API_KEY": canary,
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(canary, result.stdout + result.stderr)
        command = json.loads(result.stdout)["local_stdio_mcp_config"]["mcpServers"][
            "lean-explore"
        ]
        self.assertEqual(
            command["args"], ["mcp", "serve", "--backend", "api"]
        )
        self.assertEqual(
            command["env"], {"LEANEXPLORE_API_KEY": "<LEANEXPLORE_API_KEY>"}
        )

    def test_lean_explore_credentials_are_removed_from_ambient_environment(self) -> None:
        cli = (ROOT / "workspace/skills/lean-explore-cli/lean_explore_cli.py").read_text(
            encoding="utf-8"
        )
        mcp = (ROOT / "workspace/skills/lean-explore-mcp/lean_explore_mcp.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('os.environ.pop("LEANEXPLORE_API_KEY"', cli)
        self.assertIn('os.environ.pop("LEANEXPLORE_API_KEY"', mcp)
        self.assertNotIn('os.environ.get("LEANEXPLORE_API_KEY")', cli)
        self.assertNotIn('os.environ.get("LEANEXPLORE_API_KEY")', mcp)
        self.assertNotIn("--api-key", mcp)

    def test_calibre_and_vnthuquan_have_no_shared_secret_reader(self) -> None:
        calibre_config = ROOT / "workspace/skills/calibre/lib/config.py"
        calibre_runner = ROOT / "workspace/skills/calibre/run_cal.sh"
        vnthuquan_runner = ROOT / "workspace/skills/vnthuquan/run_vnthuquan.sh"
        vnthuquan_helper = ROOT / "workspace/skills/vnthuquan/vnthuquan_openclaw_helper.py"
        for path in (calibre_config, calibre_runner, vnthuquan_runner, vnthuquan_helper):
            text = path.read_text()
            self.assertNotIn("AAS_SECRETS_FILE", text, path)
            self.assertNotIn("OPENCLAW_SECRETS_FILE", text, path)
            self.assertNotIn(".secrets.json", text, path)
        self.assertIn("--profile calibre", calibre_runner.read_text())
        self.assertIn("env -i", vnthuquan_runner.read_text())
        self.assertNotIn("os.environ.copy()", vnthuquan_helper.read_text())

    def test_calibre_config_ignores_hostile_shared_selector_files(self) -> None:
        calibre_root = ROOT / "workspace/skills/calibre"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broad = root / "broad.json"
            broad.write_text(
                json.dumps(
                    {
                        "GDRIVE_CREDENTIALS": "broad-drive-canary",
                        "CALIBRE_GDRIVE_FOLDER_ID": "broad-folder-canary",
                    }
                )
            )
            broad.chmod(0o600)
            script = (
                "import json, sys\n"
                f"sys.path.insert(0, {str(calibre_root)!r})\n"
                "from lib.config import load_config\n"
                "config = load_config()\n"
                "print(json.dumps({\n"
                "  'credentials': config.get('GDRIVE_CREDENTIALS'),\n"
                "  'folder': config.get('gdrive_folder_id'),\n"
                "  'credential_env': 'GDRIVE_CREDENTIALS' in __import__('os').environ,\n"
                "  'folder_env': 'CALIBRE_GDRIVE_FOLDER_ID' in __import__('os').environ,\n"
                "}, sort_keys=True))\n"
            )
            environment = {
                "HOME": str(root),
                "PATH": "/usr/bin:/bin",
                "OPENCLAW_WORKSPACE": str(root / "workspace"),
                "AAS_SECRETS_FILE": str(broad),
                "OPENCLAW_SECRETS_FILE": str(broad),
            }
            result = subprocess.run(
                ["/usr/bin/python3", "-I", "-S", "-B", "-c", script],
                text=True,
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIsNone(payload["credentials"])
            self.assertNotEqual(payload["folder"], "broad-folder-canary")
            self.assertFalse(payload["credential_env"])
            self.assertFalse(payload["folder_env"])
            self.assertNotIn("broad-drive-canary", result.stdout + result.stderr)

    def test_calibre_and_zotero_subprocesses_use_minimal_environments(self) -> None:
        for relative in ("calibre/cal.py", "zotero/zot.py"):
            text = (ROOT / "workspace/skills" / relative).read_text()
            self.assertIn("_subprocess_environment", text)
            self.assertIn("env=_subprocess_environment()", text)
            self.assertIn("env=_subprocess_environment(delivery=True)", text)
            self.assertNotIn("AAS_FILE_DELIVERY_SECRETS_FILE", text)
            self.assertNotIn("REMOTE_BRIDGE_SECRETS_FILE", text)
        for relative in ("calibre/lib/config.py", "zotero/lib/config.py"):
            text = (ROOT / "workspace/skills" / relative).read_text()
            self.assertIn("os.environ.pop", text)

    def test_vnthuquan_package_child_receives_no_credential_authority(self) -> None:
        runner = ROOT / "workspace/skills/vnthuquan/run_vnthuquan.sh"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "package-env.json"
            fake_impl = root / "vnthuquan_fake.py"
            fake_impl.write_text(
                "import json, os\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text(json.dumps(dict(os.environ)))\n"
                "print('vnthuquan 0.0-test')\n"
            )
            fake = root / "vnthuquan-fake"
            fake.write_text(
                "#!/bin/sh\n"
                f"exec /usr/bin/python3 -I -S -B {str(fake_impl)!r}\n"
            )
            fake.chmod(0o700)
            environment = dict(os.environ)
            environment.update(
                {
                    "VNTHUQUAN_BIN": str(fake),
                    "VNTHUQUAN_STATE_DIR": str(root / "state"),
                    "VNTHUQUAN_RUN_DIR": str(root / "runs"),
                    "VNTHUQUAN_CACHE_DIR": str(root / "cache"),
                    "VNTHUQUAN_DOWNLOAD_DIR": str(root / "downloads"),
                    "AAS_CALIBRE_SECRETS_FILE": str(root / "calibre.json"),
                    "AAS_FILE_DELIVERY_SECRETS_FILE": str(root / "delivery.json"),
                    "REMOTE_BRIDGE_SECRETS_FILE": str(root / "bridge.json"),
                    "AAS_SECRETS_FILE": str(root / "broad.json"),
                    "OPENCLAW_SECRETS_FILE": str(root / "broad.json"),
                    "GDRIVE_CREDENTIALS": "ambient-drive-canary",
                    "TELEGRAM_BOT_TOKEN": "ambient-telegram-canary",
                    "ZULIP_API_KEY": "ambient-zulip-canary",
                    "PYTHONPATH": str(root),
                }
            )
            result = subprocess.run(
                [str(runner), "diagnose", "--json"],
                text=True,
                capture_output=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            package_environment = json.loads(marker.read_text())
            for name in (
                "AAS_CALIBRE_SECRETS_FILE",
                "AAS_FILE_DELIVERY_SECRETS_FILE",
                "REMOTE_BRIDGE_SECRETS_FILE",
                "AAS_SECRETS_FILE",
                "OPENCLAW_SECRETS_FILE",
                "GDRIVE_CREDENTIALS",
                "TELEGRAM_BOT_TOKEN",
                "ZULIP_API_KEY",
                "PYTHONPATH",
            ):
                self.assertNotIn(name, package_environment)

    def test_zotero_delivery_shims_have_dedicated_authority(self) -> None:
        base = ROOT / "workspace/skills/zotero"
        sender = (base / "send_file.sh").read_text()
        self.assertIn("AAS_FILE_DELIVERY_SECRETS_FILE", sender)
        self.assertIn("/usr/bin/python3", sender)
        self.assertIn("-I -S -B", sender)
        self.assertIn("data/send-queue", sender)
        self.assertNotIn("curl", sender)
        self.assertNotIn("OPENCLAW_BIN=", sender)
        self.assertNotIn("${AAS_SECRETS_FILE", sender)
        self.assertNotIn("${OPENCLAW_SECRETS_FILE", sender)
        telegram = (base / "send_telegram.sh").read_text()
        self.assertIn('exec /usr/bin/bash -p "$SCRIPT_DIR/send_file.sh"', telegram)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", telegram)
        self.assertNotIn("curl", telegram)

    def test_host_queue_worker_does_not_interpolate_jobs_into_python(self) -> None:
        text = (ROOT / "workspace/scripts/job_queue_worker.sh").read_text()
        email_boundary = (ROOT / "scripts/email_delivery.py").read_text()
        self.assertNotIn("AAS_SECRETS_FILE=", text)
        self.assertNotIn("OPENCLAW_SECRETS_FILE=", text)
        self.assertNotIn("python3 -c \"\n", text)
        self.assertNotIn("SEND_EMAIL_SECRETS_FILE=", text)
        self.assertIn('EMAIL_DELIVERY_HELPER="$LIBEXEC/email_delivery.py"', text)
        self.assertIn('SEND_EMAIL_SECRETS_FILE": os.fspath(smtp_snapshot)', email_boundary)
        self.assertIn("queue_boundary.snapshot", email_boundary)
        self.assertIn("renameat2", text)

    def test_send_email_ignores_retired_broad_secret_selectors(self) -> None:
        script = ROOT / "workspace/skills/send-email/send_email.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            broad = root / "broad.json"
            broad.write_text(
                json.dumps(
                    {
                        "smtp": {
                            "host": "broad-selector-canary.invalid",
                            "password": "broad-password-canary",  # LEAKSCAN-EXEMPT: synthetic fixture
                        }
                    }
                ),
                encoding="utf-8",
            )
            broad.chmod(0o600)
            environment = {
                "HOME": str(root / "home"),
                "PATH": "/usr/bin:/bin",
                "AAS_SECRETS_FILE": str(broad),
                "OPENCLAW_SECRETS_FILE": str(broad),
            }
            result = subprocess.run(
                ["/usr/bin/python3", "-I", "-S", "-B", str(script), "show-config"],
                capture_output=True,
                text=True,
                env=environment,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["password_set"])
            self.assertNotEqual(payload["host"], "broad-selector-canary.invalid")
            self.assertNotIn("broad-password-canary", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
