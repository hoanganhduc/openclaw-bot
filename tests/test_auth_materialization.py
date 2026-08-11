"""Offline regressions for lossless OpenClaw legacy credential migration."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/openclaw_auth_closure.py"
VERSION = json.loads((ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8"))[
    "openclaw"
]["observed_version"]


def run_helper(command: str, prefix: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            command,
            "--prefix",
            str(prefix),
            "--expected-version",
            VERSION,
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


class AuthMaterializationTests(unittest.TestCase):
    def test_every_configured_agent_gets_its_own_canonical_store(self) -> None:
        canaries = {
            "main": ("models.json", "models-main-provider-canary"),
            "host": ("auth-profiles.json", "profiles-host-provider-canary"),
            "review": ("auth.json", "auth-review-provider-canary"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / ".openclaw"
            prefix.mkdir(mode=0o700)
            for agent_id, (source_name, canary) in canaries.items():
                directory = prefix / "agents" / agent_id / "agent"
                directory.mkdir(parents=True, mode=0o700)
                if source_name == "models.json":
                    payload = {
                        "providers": {
                            f"provider-{agent_id}": {"apiKey": canary}
                        }
                    }
                elif source_name == "auth-profiles.json":
                    payload = {
                        "version": 1,
                        "profiles": {
                            f"provider-{agent_id}:default": {
                                "type": "api_key",
                                "provider": f"provider-{agent_id}",
                                "key": canary,
                            }
                        },
                    }
                else:
                    payload = {
                        f"provider-{agent_id}": {
                            "type": "api_key",
                            "key": canary,
                        }
                    }
                source = directory / source_name
                source.write_text(json.dumps(payload), encoding="utf-8")
                source.chmod(0o600)
            unconfigured = prefix / "agents" / "empty" / "agent"
            unconfigured.mkdir(parents=True, mode=0o700)

            result = run_helper("materialize-legacy", prefix)
            self.assertEqual(result.returncode, 0, result.stderr)
            for _source, canary in canaries.values():
                self.assertNotIn(canary, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            by_agent = {agent["agentId"]: agent for agent in report["agents"]}
            self.assertEqual(set(by_agent), {*canaries, "empty"})
            self.assertEqual(by_agent["empty"]["status"], "UNCONFIGURED")
            self.assertFalse(by_agent["empty"]["canonicalStore"]["exists"])
            for agent_id, (_source, canary) in canaries.items():
                with self.subTest(agent_id=agent_id):
                    self.assertEqual(by_agent[agent_id]["status"], "MATERIALIZED")
                    self.assertTrue(by_agent[agent_id]["canonicalStore"]["exists"])
                    database = (
                        prefix / "agents" / agent_id / "agent/openclaw-agent.sqlite"
                    )
                    with sqlite3.connect(database) as connection:
                        owner = connection.execute(
                            "SELECT role, agent_id, schema_version, app_version "
                            "FROM schema_meta WHERE meta_key='primary'"
                        ).fetchone()
                        store = json.loads(
                            connection.execute(
                                "SELECT store_json FROM auth_profile_store "
                                "WHERE store_key='primary'"
                            ).fetchone()[0]
                        )
                    self.assertEqual(owner, ("agent", agent_id, 1, None))
                    self.assertIn(
                        canary,
                        {
                            credential.get("key")
                            for credential in store["profiles"].values()
                        },
                    )

    def test_unique_models_key_survives_materialization_without_output(self) -> None:
        profile_canary = "profile-secret-canary-never-print"
        models_canary = "models-secret-canary-never-print"
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / ".openclaw"
            agent = prefix / "agents/main/agent"
            agent.mkdir(parents=True)
            prefix.chmod(0o700)
            (prefix / "agents").chmod(0o700)
            (prefix / "agents/main").chmod(0o775)
            agent.chmod(0o775)
            (prefix / "agents/sandbox").symlink_to("main")
            auth = agent / "auth-profiles.json"
            auth.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profiles": {
                            "fixture:default": {
                                "type": "api_key",
                                "provider": "fixture",
                                "key": profile_canary,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            models = agent / "models.json"
            models.write_text(
                json.dumps(
                    {
                        "providers": {
                            "fixture": {"apiKey": models_canary},
                            "placeholder": {"apiKey": "{{ REDACTED }}"},
                            "header-only": {
                                "headers": {"Accept": "application/json"}
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            auth.chmod(0o664)
            models.chmod(0o664)

            first = run_helper("materialize-legacy", prefix)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotIn(profile_canary, first.stdout + first.stderr)
            self.assertNotIn(models_canary, first.stdout + first.stderr)
            report = json.loads(first.stdout)
            self.assertEqual(report["schema"], "openclaw.agent-auth-materialization/v2")
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["expectedVersion"], VERSION)
            self.assertEqual(report["schemaVersion"], 1)
            self.assertFalse(report["openclawExecuted"])
            self.assertFalse(report["networkEnabled"])
            self.assertEqual(report["profilesImported"], 2)
            self.assertEqual(report["modelCredentialsImported"], 1)
            self.assertEqual(report["redactionMarkersSkipped"], 1)
            self.assertEqual(report["layoutNormalization"]["aliasesRemoved"], 1)
            self.assertFalse((prefix / "agents/sandbox").exists())
            self.assertEqual(
                stat.S_IMODE((prefix / "agents/main").stat().st_mode), 0o700
            )
            self.assertEqual(stat.S_IMODE(agent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(models.stat().st_mode), 0o600)

            database = agent / "openclaw-agent.sqlite"
            with sqlite3.connect(database) as connection:
                store = json.loads(
                    connection.execute(
                        "SELECT store_json FROM auth_profile_store WHERE store_key='primary'"
                    ).fetchone()[0]
                )
                state = json.loads(
                    connection.execute(
                        "SELECT state_json FROM auth_profile_state WHERE state_key='primary'"
                    ).fetchone()[0]
                )
                owner = connection.execute(
                    "SELECT role, agent_id, schema_version, app_version "
                    "FROM schema_meta WHERE meta_key='primary'"
                ).fetchone()
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (1,))
            self.assertEqual(owner, ("agent", "main", 1, None))
            fixture_profiles = {
                profile_id: credential
                for profile_id, credential in store["profiles"].items()
                if credential.get("provider") == "fixture"
            }
            self.assertEqual(len(fixture_profiles), 2)
            self.assertEqual(
                {credential.get("key") for credential in fixture_profiles.values()},
                {profile_canary, models_canary},
            )
            selected = state["lastGood"]["fixture"]
            self.assertEqual(store["profiles"][selected]["key"], models_canary)

            second = run_helper("materialize-legacy", prefix)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["profilesImported"], 0)
            self.assertNotIn(profile_canary, second.stdout + second.stderr)
            self.assertNotIn(models_canary, second.stdout + second.stderr)

    def test_alias_preflight_conflict_leaves_link_and_target_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / ".openclaw"
            main = prefix / "agents/main/agent"
            main.mkdir(parents=True)
            prefix.chmod(0o700)
            (prefix / "agents/sandbox").symlink_to("../outside")
            result = run_helper("materialize-legacy", prefix)
            self.assertEqual(result.returncode, 2)
            self.assertTrue((prefix / "agents/sandbox").is_symlink())
            self.assertTrue(main.is_dir())
            self.assertEqual(json.loads(result.stdout)["status"], "FAIL")

    def test_sensitive_models_header_fails_without_partial_database(self) -> None:
        header_canary = "header-secret-canary-never-print"
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / ".openclaw"
            agent = prefix / "agents/main/agent"
            agent.mkdir(parents=True)
            prefix.chmod(0o700)
            models = agent / "models.json"
            models.write_text(
                json.dumps(
                    {
                        "providers": {
                            "fixture": {
                                "headers": {"Authorization": header_canary}
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            models.chmod(0o600)
            result = run_helper("materialize-legacy", prefix)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(header_canary, result.stdout + result.stderr)
            self.assertFalse((agent / "openclaw-agent.sqlite").exists())

    def test_placeholder_cleanup_does_not_quarantine_real_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / ".openclaw"
            agent = prefix / "agents/main/agent"
            agent.mkdir(parents=True)
            prefix.chmod(0o700)
            real = agent / "auth-profiles.json"
            real.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profiles": {
                            "fixture:default": {
                                "type": "api_key",
                                "provider": "fixture",
                                "key": "real-offline-canary",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            placeholder = agent / "models.json"
            placeholder.write_text(
                '{"providers":{"fixture":{"apiKey":"{{ REDACTED }}"}}}',
                encoding="utf-8",
            )
            real.chmod(0o600)
            placeholder.chmod(0o600)
            result = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "quarantine-placeholders",
                    "--prefix",
                    str(prefix),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(real.exists())
            self.assertFalse(placeholder.exists())
            self.assertEqual(json.loads(result.stdout)["fileCount"], 1)


if __name__ == "__main__":
    unittest.main()
