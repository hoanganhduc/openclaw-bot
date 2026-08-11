"""Render a bounded SceneSpec to a normalized, silent interlude clip."""

from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import scenegen
from .model import SceneSpec


QUALITY_VALUES = frozenset({"-ql", "-qm", "-qh", "-qk", "-qp"})
SCENE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class ToolMissing(RuntimeError):
    pass


def _trusted_executable(path: Path, name: str) -> str:
    try:
        information = path.stat(follow_symlinks=True)
        named = path.lstat()
    except FileNotFoundError as exc:
        raise ToolMissing(f"{name} is unavailable at the reviewed path") from exc
    if (
        not stat.S_ISREG(information.st_mode)
        or not os.access(path, os.X_OK)
        or information.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(information.st_mode) & 0o022
        or (stat.S_ISLNK(named.st_mode) and not path.resolve().is_file())
    ):
        raise ToolMissing(f"{name} executable is unsafe")
    return os.fspath(path)


def manim_bin() -> str:
    return _trusted_executable(Path(sys.prefix) / "bin" / "manim", "manim")


def ffmpeg_bin() -> str:
    return _trusted_executable(Path("/usr/bin/ffmpeg"), "ffmpeg")


def build_manim_args(
    script: str,
    scene: str,
    media_dir: str,
    quality: str = "-qh",
    manim: str = "manim",
) -> list[str]:
    return [
        manim,
        "render",
        quality,
        "--format=mp4",
        "--media_dir",
        media_dir,
        script,
        scene,
    ]


def build_normalize_args(
    src: str,
    dst: str,
    width: int,
    height: int,
    fps: int,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"fps={fps},format=yuv420p"
    )
    return [
        ffmpeg,
        "-y",
        "-i",
        src,
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        dst,
    ]


def _render_environment(work: Path) -> dict[str, str]:
    home = work / "home"
    temporary = work / "tmp"
    cache = work / "cache"
    tex_output = work / "tex-output"
    for directory in (home, temporary, cache, tex_output):
        directory.mkdir(mode=0o700)
    return {
        "HOME": os.fspath(home),
        "TMPDIR": os.fspath(temporary),
        "XDG_CACHE_HOME": os.fspath(cache),
        "TEXMFOUTPUT": os.fspath(tex_output),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "openin_any": "p",
        "openout_any": "p",
        "shell_escape": "0",
    }


def _run_bounded(
    argv: list[str], *, timeout: int, environment: dict[str, str], cwd: Path
) -> None:
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, argv)


def render(
    spec: SceneSpec,
    out_path: str,
    quality: str = "-qh",
    scene: str = "GeneratedScene",
) -> Path:
    spec.validate()
    if quality not in QUALITY_VALUES:
        raise ValueError("unsupported Manim quality")
    if SCENE_NAME_RE.fullmatch(scene) is None:
        raise ValueError("invalid Manim scene name")
    runtime_tmp = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    with tempfile.TemporaryDirectory(prefix="mma-render-", dir=runtime_tmp) as name:
        work = Path(name)
        work.chmod(0o700)
        script = work / "scene.py"
        script.write_text(scenegen.generate_scene(spec, scene), encoding="utf-8")
        script.chmod(0o600)
        media_dir = work / "media"
        environment = _render_environment(work)
        _run_bounded(
            build_manim_args(
                str(script), scene, str(media_dir), quality, manim_bin()
            ),
            timeout=840,
            environment=environment,
            cwd=work,
        )
        produced = sorted(media_dir.glob(f"videos/**/{scene}.mp4"))
        if not produced:
            raise RuntimeError("manim did not produce an output mp4")
        information = produced[-1].lstat()
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 1
            or information.st_size > 2 * 1024 * 1024 * 1024
        ):
            raise RuntimeError("manim produced an unsafe output")
        _run_bounded(
            build_normalize_args(
                str(produced[-1]),
                out_path,
                spec.width,
                spec.height,
                spec.fps,
                ffmpeg_bin(),
            ),
            timeout=120,
            environment=environment,
            cwd=work,
        )
    return Path(out_path)
