"""Security regressions for descriptor-bound RSS summary publication."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "rss_summary_publish_test", ROOT / "scripts/rss_summary_publish.py"
)
assert SPEC is not None and SPEC.loader is not None
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class RssSummaryPublishTests(unittest.TestCase):
    def workspace(self, root: Path) -> tuple[Path, Path, Path]:
        workspace = root / "workspace"
        digests = workspace / "data/research/rss/digests"
        sessions = workspace / "data/sessions"
        digests.mkdir(parents=True, mode=0o700)
        sessions.mkdir(parents=True, mode=0o700)
        return workspace, digests, sessions

    def test_existing_summary_symlinks_are_replaced_without_following(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, digests, sessions = self.workspace(root)
            session = sessions / "current"
            session.mkdir(mode=0o700)
            (digests / "rss-research.md").write_text(
                "# Digest\n## 1. Safe heading\n## 2. Second heading\n",
                encoding="utf-8",
            )
            victim = root / "victim"
            victim.write_text("unchanged\n", encoding="utf-8")
            timestamp = "20260811T120000Z"
            for path in (
                session / "last-summary.md",
                session / f"summary-{timestamp}.md",
                digests / f"summary-{timestamp}.md",
            ):
                path.symlink_to(victim)

            relative = publisher.publish(
                workspace,
                now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(relative, "data/sessions/current/last-summary.md")
            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged\n")
            for path in (
                session / "last-summary.md",
                session / f"summary-{timestamp}.md",
                digests / f"summary-{timestamp}.md",
            ):
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertIn("- Safe heading", path.read_text(encoding="utf-8"))

    def test_open_session_descriptor_survives_parent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _workspace, _digests, sessions = self.workspace(root)
            selected = sessions / "current"
            selected.mkdir(mode=0o700)
            sessions_descriptor = publisher._open_directory(sessions)
            session_descriptor, name = publisher._select_session(sessions_descriptor)
            self.assertEqual(name, "current")
            detached = sessions / "detached"
            selected.rename(detached)
            selected.mkdir(mode=0o700)
            victim = root / "victim"
            victim.write_text("unchanged\n", encoding="utf-8")
            (selected / "last-summary.md").symlink_to(victim)
            try:
                publisher._write_atomic(session_descriptor, "last-summary.md", b"safe\n")
            finally:
                os.close(session_descriptor)
                os.close(sessions_descriptor)

            self.assertEqual((detached / "last-summary.md").read_bytes(), b"safe\n")
            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged\n")
            self.assertTrue((selected / "last-summary.md").is_symlink())

    def test_symlinked_digest_and_session_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, digests, sessions = self.workspace(root)
            real = sessions / "real"
            real.mkdir(mode=0o700)
            (sessions / "newer-link").symlink_to(real, target_is_directory=True)
            outside = root / "outside.md"
            outside.write_text("## 1. Injected heading\n", encoding="utf-8")
            (digests / "rss-research.md").symlink_to(outside)

            publisher.publish(
                workspace,
                now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
            )

            summary = (real / "last-summary.md").read_text(encoding="utf-8")
            self.assertNotIn("Injected heading", summary)

    def test_service_and_runner_enforce_the_hardened_boundary(self) -> None:
        runner = (ROOT / "workspace/skills/rss-news-digest/run_and_summarize.sh").read_text(
            encoding="utf-8"
        )
        service = (ROOT / "systemd/user/rss_news_digest_bot.service").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("/usr/bin/cp", runner)
        self.assertNotIn("/usr/bin/rm", runner)
        self.assertIn("rss_summary_publish.py", runner)
        self.assertIn("--require-existing-feeds", runner)
        self.assertIn("--no-write-digest-stubs", runner)
        self.assertIn('RUNTIME_CREDENTIALS="/run/user/${UID}/credentials"', runner)
        self.assertIn('[[ -r "$RUNTIME_CREDENTIALS" ]]', runner)
        for directive in (
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=tmpfs",
            "PrivateTmp=true",
            "ReadOnlyPaths=/run",
            "BindPaths={{ OPENCLAW_WORKSPACE }}/data/research/rss",
            "BindPaths={{ OPENCLAW_WORKSPACE }}/data/sessions",
        ):
            self.assertIn(directive, service)


if __name__ == "__main__":
    unittest.main()
