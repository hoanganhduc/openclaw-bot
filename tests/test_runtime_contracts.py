"""Regression tests for OpenClaw skill and sandbox runtime contracts."""

from __future__ import annotations

from pathlib import Path
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


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
        assignment = next(
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith("local container_sage=")
        )
        self.assertIn('/workspace/data/job-queue/${job_id}.sage', assignment)
        self.assertNotIn("PRIVATE_DATA_DIR", assignment)

    def test_sage_container_mounts_only_the_job_queue(self) -> None:
        source = (
            ROOT / "workspace/scripts/job_queue_worker.sh"
        ).read_text(encoding="utf-8")
        self.assertIn('-v "$JOB_QUEUE:/workspace/data/job-queue"', source)
        self.assertNotIn('-v "$WORKSPACE:/workspace"', source)
        self.assertIn('chmod 1777 "$JOB_QUEUE"', source)

    def test_shared_queue_worker_is_neutral_runtime_infrastructure(self) -> None:
        service = (ROOT / "systemd/user/send-queue-worker.service").read_text(
            encoding="utf-8"
        )
        compatibility = (
            ROOT / "workspace/skills/zotero/job_queue_worker.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("/scripts/job_queue_worker.sh", service)
        self.assertNotIn("/skills/zotero/job_queue_worker.sh", service)
        self.assertIn("../../scripts/job_queue_worker.sh", compatibility)

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
        for source, environment in (
            (docling, "docling-cpu"),
            (lean, "lean-explore"),
        ):
            with self.subTest(environment=environment):
                self.assertIn(
                    f"/opt/coding-system/python-closure/{environment}", source
                )
                self.assertIn(
                    f".local/share/coding-system/python-closure/{environment}",
                    source,
                )
                self.assertIn('"$HOME" == "/workspace"', source)
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
        self.assertIn('workspace_exclude="$WORKSPACE/.git/info/exclude"', install)

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
        for agent in [config["agents"]["defaults"], *config["agents"]["list"]]:
            docker = agent.get("sandbox", {}).get("docker")
            if not docker:
                continue
            self.assertIn("@sha256:", docker["image"])
            self.assertEqual(docker["pidsLimit"], 512)
            self.assertEqual(docker["memory"], "4g")
            self.assertEqual(docker["memorySwap"], "4g")
            self.assertEqual(docker["cpus"], 2)

    def test_installer_materializes_npm_projects_convergently(self) -> None:
        source = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('render_copy "$SCRIPT_DIR/npm" "$PREFIX/npm"', source)
        self.assertIn("CONVERGENT", source)
        self.assertNotIn('preview = path.with_name(path.name + ".new")', source)
        self.assertIn('chmod 0600 "$auth_profiles"', source)
        self.assertIn('-c user.name="OpenClaw Restore"', source)
        self.assertIn('-c user.email="openclaw-restore@localhost"', source)
        self.assertNotIn(
            'commit -m "Initialize OpenClaw workspace rollback baseline" >/dev/null || true',
            source,
        )

    def test_owner_backup_is_link_free_and_restore_is_bounded(self) -> None:
        backup = (ROOT / "backup.sh").read_text(encoding="utf-8")
        restore = (ROOT / "restore.sh").read_text(encoding="utf-8")
        self.assertIn("find -P", backup)
        self.assertIn("--no-recursion", backup)
        self.assertIn("--hard-dereference", backup)
        self.assertIn("member.islnk()", backup)
        self.assertIn("member.issym()", backup)
        self.assertIn("member_count > 1_000_000", restore)
        self.assertIn("20 * 1024 * 1024 * 1024", restore)
        self.assertIn("restore destination contains a symlink", restore)
        self.assertIn("restore file collides with a symlink", restore)
        self.assertIn("os.replace(temporary_name, destination)", restore)
        self.assertNotIn('cp -a "$STAGE"/. "$PREFIX"/', restore)

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
