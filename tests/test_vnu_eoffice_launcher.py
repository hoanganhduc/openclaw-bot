"""Keep VNU eOffice launchers aligned with the sandbox-visible checkout."""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class TestVnuEofficeLauncher(unittest.TestCase):
    def test_host_and_sandbox_defaults_share_workspace_checkout(self):
        for relative in (
            "workspace/skills/vnu-eoffice/run_vnu_eoffice.sh",
            "root-skills/vnu-eoffice/run_vnu_eoffice.sh",
            "workspace/skills/vnu-eoffice/vnu_eoffice_openclaw.py",
            "root-skills/vnu-eoffice/vnu_eoffice_openclaw.py",
        ):
            with self.subTest(relative=relative):
                source = (REPO / relative).read_text(encoding="utf-8")
                self.assertIn("{{ OPENCLAW_WORKSPACE }}/vnueoffice_repo", source)
                self.assertNotIn("{{ USER_HOME }}/vnueoffice", source)

    def test_sandbox_data_uses_writable_workspace_mount(self):
        for relative in (
            "workspace/skills/vnu-eoffice/run_vnu_eoffice.sh",
            "root-skills/vnu-eoffice/run_vnu_eoffice.sh",
        ):
            with self.subTest(relative=relative):
                source = (REPO / relative).read_text(encoding="utf-8")
                self.assertIn(
                    'VNU_OPENCLAW_DATA_DIR:-/workspace/data/vnu_eoffice', source
                )
                self.assertNotIn(
                    'VNU_OPENCLAW_DATA_DIR:-{{ PRIVATE_DATA_DIR }}/vnu_eoffice',
                    source,
                )

    def test_skill_routes_documents_through_helper_queue(self):
        for relative in (
            "workspace/skills/vnu-eoffice/SKILL.md",
            "root-skills/vnu-eoffice/SKILL.md",
        ):
            with self.subTest(relative=relative):
                source = (REPO / relative).read_text(encoding="utf-8")
                self.assertIn("Never emit a raw `MEDIA:` directive", source)
                self.assertIn("/workspace/data/vnu_eoffice/documents", source)
                self.assertNotIn("{{ PRIVATE_DATA_DIR }}/vnu_eoffice", source)

    def test_vnu_has_no_direct_telegram_authority_or_notifier(self):
        for relative in (
            "workspace/skills/vnu-eoffice/run_vnu_eoffice.sh",
            "root-skills/vnu-eoffice/run_vnu_eoffice.sh",
        ):
            source = (REPO / relative).read_text(encoding="utf-8")
            self.assertIn("unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID", source)
        for relative in (
            "workspace/skills/vnu-eoffice/vnu_eoffice_openclaw.py",
            "root-skills/vnu-eoffice/vnu_eoffice_openclaw.py",
        ):
            source = (REPO / relative).read_text(encoding="utf-8")
            self.assertNotIn("TelegramNotifier", source)
            self.assertNotIn("send_documents", source)
            self.assertNotIn("--send-telegram", source)
            self.assertIn('DELIVERY_WORKSPACE / "skills" / "zotero" / "send_file.sh"', source)
            self.assertIn("--delivery-target", source)

    def test_launchers_pin_delivery_workspace_to_current_trust_side(self):
        for relative in (
            "workspace/skills/vnu-eoffice/run_vnu_eoffice.sh",
            "root-skills/vnu-eoffice/run_vnu_eoffice.sh",
        ):
            with self.subTest(relative=relative):
                source = (REPO / relative).read_text(encoding="utf-8")
                self.assertIn("export OPENCLAW_WORKSPACE=/workspace", source)
                self.assertIn(
                    'export OPENCLAW_WORKSPACE="{{ OPENCLAW_WORKSPACE }}"', source
                )

    def test_host_delivery_accepts_only_bounded_vnu_document_root(self):
        source = (REPO / "scripts/file_delivery.py").read_text(encoding="utf-8")
        self.assertIn(
            'workspace / "data" / "vnu_eoffice" / "documents"', source
        )
        self.assertNotIn('workspace / "secrets" / "vnu-eoffice"', source)


if __name__ == "__main__":
    unittest.main()
