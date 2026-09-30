#!/usr/bin/env python3
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Vendor-installed skill trees: refetched by their installer, and their docs carry
# example account and zone identifiers that must not look like owner data.
VENDOR_SKILLS = (
    "cloudflare", "cloudflare-email-service", "cloudflare-one", "cloudflare-one-migrations",
    "agents-sdk", "durable-objects", "sandbox-sdk", "turnstile-spin", "web-perf",
    "workers-best-practices", "wrangler",
)


class SyncSanitizerTests(unittest.TestCase):
    def sync(self, root: Path) -> Path:
        home = root / "home"
        prefix = home / ".openclaw"
        for path in (
            prefix / "workspace/scripts",
            prefix / "workspace/openclaw-scripts",
            prefix / "npm/projects",
            home / ".config/systemd/user",
        ):
            path.mkdir(parents=True, exist_ok=True)
        (prefix / "openclaw.json").write_text(
            json.dumps({"channels": {"googlechat": {
                "enabled": True,
                "appPrincipal": "123456789012345678901",
                "webhookPath": "/googlechat",
            }}}),
            encoding="utf-8",
        )
        for skill in (*VENDOR_SKILLS, "own-skill"):
            (prefix / "skills" / skill).mkdir(parents=True)
            (prefix / "skills" / skill / "SKILL.md").write_text(f"# {skill}\n", encoding="utf-8")
        staging = root / "staging"
        result = subprocess.run(
            ["/usr/bin/bash", str(ROOT / "sync.sh"), "--dry-run",
             "--prefix", str(prefix), "--staging", str(staging)],
            env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return staging

    def test_google_chat_app_principal_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = self.sync(Path(temporary))
            text = (staging / "config/openclaw.json.template").read_text(encoding="utf-8")
            self.assertNotIn("123456789012345678901", text)
            googlechat = json.loads(text)["channels"]["googlechat"]
            self.assertEqual(googlechat["appPrincipal"], "{{ REDACTED }}")
            self.assertEqual(googlechat["webhookPath"], "/googlechat")

    def test_vendor_skills_are_not_synced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = self.sync(Path(temporary))
            for skill in VENDOR_SKILLS:
                with self.subTest(skill=skill):
                    self.assertFalse((staging / "root-skills" / skill).exists())
            self.assertTrue((staging / "root-skills/own-skill/SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
