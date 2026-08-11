"""Regression tests for privileged Bash entrypoints and hostile environments."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_host_exec():
    path = ROOT / "scripts/host_exec.py"
    specification = importlib.util.spec_from_file_location(
        "openclaw_privileged_shell_host_exec", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class PrivilegedShellTests(unittest.TestCase):
    def test_owner_entrypoint_help_ignores_bash_env_path_and_exported_functions(self) -> None:
        for relative in (
            "backup.sh",
            "restore.sh",
            "install.sh",
            "sync.sh",
            "deploy.sh",
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bash_env_marker = root / "bash-env-ran"
                function_marker = root / "exported-function-ran"
                path_marker = root / "attacker-path-ran"
                bash_env = root / "bash-env"
                bash_env.write_text(
                    f"/usr/bin/touch {shlex.quote(str(bash_env_marker))}\n",
                    encoding="utf-8",
                )
                attacker_bin = root / "bin"
                attacker_bin.mkdir(mode=0o700)
                fake_cat = attacker_bin / "cat"
                fake_cat.write_text(
                    "#!/bin/sh\n"
                    f"/usr/bin/touch {shlex.quote(str(path_marker))}\n",
                    encoding="utf-8",
                )
                fake_cat.chmod(0o700)
                environment = {
                    **os.environ,
                    "BASH_ENV": str(bash_env),
                    "BASH_FUNC_cat%%": (
                        "() { /usr/bin/touch "
                        + shlex.quote(str(function_marker))
                        + "; }"
                    ),
                    "PATH": str(attacker_bin),
                    "PYTHONPATH": str(root / "attacker-python"),
                    "NODE_OPTIONS": "--require=/does/not/exist",
                }
                completed = subprocess.run(
                    [str(ROOT / relative), "--help"],
                    capture_output=True,
                    text=True,
                    env=environment,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertLess(len(completed.stdout.encode("utf-8")), 8192)
                self.assertFalse(bash_env_marker.exists())
                self.assertFalse(function_marker.exists())
                self.assertFalse(path_marker.exists())

    def test_owner_state_lock_reexec_preserves_privileged_mode(self) -> None:
        helper = ROOT / "scripts/owner_state_lock.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe = root / "probe.sh"
            result = root / "flags"
            bash_env_marker = root / "bash-env-ran"
            function_marker = root / "function-ran"
            bash_env = root / "bash-env"
            bash_env.write_text(
                f"/usr/bin/touch {shlex.quote(str(bash_env_marker))}\n",
                encoding="utf-8",
            )
            probe.write_text(
                "#!/usr/bin/bash -p\n"
                "if [[ \"$-\" != *p* ]]; then exit 71; fi\n"
                "printf '%s\\n' \"$-\" > \"$1\"\n",
                encoding="utf-8",
            )
            probe.chmod(0o700)
            environment = {
                **os.environ,
                "BASH_ENV": str(bash_env),
                "BASH_FUNC_printf%%": (
                    "() { /usr/bin/touch "
                    + shlex.quote(str(function_marker))
                    + "; }"
                ),
            }
            completed = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-S",
                    "-B",
                    str(helper),
                    "--lock-path",
                    str(root / "owner.lock"),
                    "--",
                    str(probe),
                    str(result),
                ],
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("p", result.read_text(encoding="utf-8").strip())
            self.assertFalse(bash_env_marker.exists())
            self.assertFalse(function_marker.exists())

    def test_attested_bash_launcher_forces_privileged_no_profile_mode(self) -> None:
        launcher = load_host_exec()

        class ExecObserved(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "artifact.sh"
            artifact.write_text("#!/usr/bin/bash\nexit 0\n", encoding="utf-8")
            descriptor = os.open(artifact, os.O_RDONLY)
            original_umask = os.umask(0)
            os.umask(original_umask)
            try:
                hostile = {
                    **os.environ,
                    "BASH_ENV": str(Path(temporary) / "bash-env"),
                    "BASH_FUNC_cat%%": "() { :; }",
                    "PYTHONPATH": str(Path(temporary) / "python"),
                    "LD_PRELOAD": str(Path(temporary) / "preload.so"),
                }
                with mock.patch.object(
                    launcher, "_validate_generation_path", return_value=Path(temporary)
                ), mock.patch.object(
                    launcher, "_verify_and_select", return_value=(descriptor, "bash")
                ), mock.patch.object(
                    launcher.os,
                    "execve",
                    side_effect=ExecObserved("captured"),
                ) as execute, mock.patch.object(
                    launcher.sys,
                    "argv",
                    [
                        "host_exec.py",
                        "--generation",
                        temporary,
                        "--artifact",
                        "artifact.sh",
                        "--",
                        "--fixture",
                    ],
                ), mock.patch.dict(launcher.os.environ, hostile, clear=True):
                    with self.assertRaises(ExecObserved):
                        launcher.main()
                executable, arguments, environment = execute.call_args.args
                self.assertEqual(executable, "/usr/bin/bash")
                self.assertEqual(
                    arguments[:5],
                    [
                        "/usr/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-p",
                        f"/proc/self/fd/{descriptor}",
                    ],
                )
                self.assertEqual(arguments[5:], ["--fixture"])
                self.assertEqual(environment["PATH"], "/usr/bin:/bin")
                for name in (
                    "BASH_ENV",
                    "BASH_FUNC_cat%%",
                    "PYTHONPATH",
                    "LD_PRELOAD",
                ):
                    self.assertNotIn(name, environment)
            finally:
                os.umask(original_umask)
                os.close(descriptor)

    def test_security_sensitive_shell_sources_require_privileged_bash(self) -> None:
        relatives = (
            "backup.sh",
            "restore.sh",
            "install.sh",
            "sync.sh",
            "deploy.sh",
            "workspace/scripts/job_queue_worker.sh",
            "workspace/scripts/moltbook-relay.sh",
            "workspace/skills/rss-news-digest/run_and_summarize.sh",
            "workspace/skills/manim-math-animation/run_manim_math_animation.sh",
            "workspace/skills/zotero/send_file.sh",
            "workspace/skills/zotero/send_telegram.sh",
            "workspace/skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            "workspace/skills/lean-explore-cli/run_lean_explore.sh",
            "workspace/skills/lean-explore-mcp/run_lean_explore_mcp.sh",
            "workspace/skills/research-digest-wrapper/run_research_digest.sh",
            "workspace/skills/submission-venue-selector/run_submission_venue_selector.sh",
            "workspace/skills/zotero/run_zot.sh",
            "workspace/skills/calibre/run_cal.sh",
        )
        for relative in relatives:
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertTrue(source.startswith("#!/usr/bin/bash -p\n"))
                self.assertIn('if [[ "$-" != *p* ]]', source)
                self.assertIn("unset BASH_ENV ENV CDPATH GLOBIGNORE", source)
                self.assertIn("PATH=/usr/bin:/bin", source)


if __name__ == "__main__":
    unittest.main()
