from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "workspace" / "skills" / "manim-math-animation"
sys.path.insert(0, os.fspath(RUNTIME))

from mma import render  # noqa: E402
from mma.model import SceneSpec  # noqa: E402


class ManimIsolationTests(unittest.TestCase):
    def test_unsafe_tex_file_shell_and_dynamic_control_primitives_are_rejected(self) -> None:
        dangerous = (
            r"\input{/etc/passwd}",
            r"\@@input secrets",
            r"\csname input\endcsname",
            r"^^5cinput secrets",
            r"\directlua{os.execute('id')}",
            r"\pdffiledump file{/etc/passwd}",
            r"\begin{document}escape",
            r"\write18{id}",
        )
        for equation in dangerous:
            with self.subTest(equation=equation):
                with self.assertRaises(ValueError):
                    SceneSpec(equations=[equation]).validate()

        safe = SceneSpec(equations=[r"A = \begin{pmatrix}1&0\\0&1\end{pmatrix}"])
        safe.validate()

    def test_scene_shape_time_text_and_frame_resources_are_bounded(self) -> None:
        invalid = (
            {"equations": ["x"] * 33},
            {"equations": ["x" * 4097]},
            {"equations": ["x"], "width": True},
            {"equations": ["x"], "fps": 61},
            {"equations": ["x"] * 13, "run_time": 15.0},
            {"equations": ["x"], "background": "../../secret"},
            {"equations": ["x"], "font": "/tmp/attacker-font"},
            {"equations": ["x"], "unexpected": "field"},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    SceneSpec.from_dict(payload)

    def test_renderer_child_environment_is_minimal_and_tex_paranoid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = render._render_environment(Path(temporary))
        self.assertEqual(
            set(environment),
            {
                "HOME",
                "TMPDIR",
                "XDG_CACHE_HOME",
                "TEXMFOUTPUT",
                "PATH",
                "LANG",
                "LC_ALL",
                "openin_any",
                "openout_any",
                "shell_escape",
            },
        )
        self.assertEqual(environment["PATH"], "/usr/bin:/bin")
        self.assertEqual(environment["openin_any"], "p")
        self.assertEqual(environment["openout_any"], "p")
        self.assertEqual(environment["shell_escape"], "0")
        source = (RUNTIME / "mma" / "render.py").read_text(encoding="utf-8")
        self.assertNotIn('os.environ.get("MANIM")', source)
        self.assertNotIn('os.environ.get("FFMPEG")', source)
        self.assertIn('Path("/usr/bin/ffmpeg")', source)

    def test_renderer_timeout_kills_the_spawned_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            environment = {
                "HOME": temporary,
                "TMPDIR": temporary,
                "PATH": "/usr/bin:/bin",
            }
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                render._run_bounded(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        "-B",
                        "-c",
                        "import time; time.sleep(30)",
                    ],
                    timeout=1,
                    environment=environment,
                    cwd=work,
                )
            self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
