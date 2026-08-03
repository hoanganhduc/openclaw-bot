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


if __name__ == "__main__":
    unittest.main()
