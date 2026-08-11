#!/usr/bin/env python3
"""Launch one reviewed skill with its compiled credential projection.

Only a profile name and arguments for that profile are caller-controlled.  The
secret authority, projected keys, Python script, and import roots are compiled
below; callers cannot turn this helper into a generic credential injector or
an arbitrary Python launcher.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import runpy
import stat
import sys
from typing import Mapping, NamedTuple


MAX_SECRET_FILE_BYTES = 65_536
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
SAFE_PATH = "/usr/bin:/bin"
SAFE_INHERITED_ENV = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TZ",
    }
)


class Profile(NamedTuple):
    selector: str
    authority: str
    keys: frozenset[str]
    script: str
    import_roots: tuple[str, ...] = ()
    json_only: bool = False


PROFILES: dict[str, Profile] = {
    "axiom-axle-mcp": Profile(
        "AAS_AXLE_SECRETS_FILE",
        "home:.config/ai-agents-skills/axiom-axle.env",
        frozenset({"AXLE_API_KEY"}),
        "axiom-axle-mcp/axiom_axle_mcp.py",
    ),
    "calibre": Profile(
        "AAS_CALIBRE_SECRETS_FILE",
        "workspace:.config/ai-agents-skills/calibre-secrets.json",
        frozenset({"CALIBRE_GDRIVE_FOLDER_ID", "GDRIVE_CREDENTIALS"}),
        "calibre/cal.py",
        ("workspace:.local/lib/python{python}/site-packages", "workspace:.local"),
        json_only=True,
    ),
    "lean-explore-cli": Profile(
        "AAS_LEANEXPLORE_SECRETS_FILE",
        "home:.config/ai-agents-skills/lean-explore.env",
        frozenset({"LEANEXPLORE_API_KEY"}),
        "lean-explore-cli/lean_explore_cli.py",
        ("lean-closure:site-packages",),
    ),
    "lean-explore-mcp": Profile(
        "AAS_LEANEXPLORE_SECRETS_FILE",
        "home:.config/ai-agents-skills/lean-explore.env",
        frozenset({"LEANEXPLORE_API_KEY"}),
        "lean-explore-mcp/lean_explore_mcp.py",
        ("lean-closure:site-packages",),
    ),
    "research-digest": Profile(
        "AAS_RESEARCH_DIGEST_SECRETS_FILE",
        "home:.config/ai-agents-skills/research-digest.env",
        frozenset({"OPENCLAW_S2_API_KEY"}),
        "research-digest-wrapper/research_digest.py",
        ("workspace:.local/lib/python{python}/site-packages", "workspace:.local"),
    ),
    "submission-venue-selector": Profile(
        "AAS_SUBMISSION_VENUE_SECRETS_FILE",
        "home:.config/ai-agents-skills/submission-venue.env",
        frozenset({"SEMANTIC_SCHOLAR_API_KEY", "UNPAYWALL_EMAIL"}),
        "submission-venue-selector/submission_venue_selector.py",
    ),
    "zotero": Profile(
        "AAS_ZOTERO_SKILL_SECRETS_FILE",
        "workspace:.config/ai-agents-skills/zotero-secrets.json",
        frozenset(
            {
                "GDRIVE_CREDENTIALS",
                "SEMANTIC_SCHOLAR_API_KEY",
                "WEBDAV_PASSWORD",
                "ZOTERO_API_KEY",
            }
        ),
        "zotero/zot.py",
        ("workspace:.local/lib/python{python}/site-packages", "workspace:.local"),
    ),
}

SECRET_PROJECTIONS = {profile.selector: profile.keys for profile in PROFILES.values()}
GLOBAL_ALLOWED_KEYS = frozenset().union(*(profile.keys for profile in PROFILES.values()))
GLOBAL_SECRET_SELECTORS = frozenset(SECRET_PROJECTIONS)
FORBIDDEN_SHARED_SELECTORS = frozenset(
    {"AAS_SECRETS_FILE", "OPENCLAW_SECRETS_FILE", "AAS_SKILL_SECRETS_FILE"}
)
ALLOWED_KEYS = GLOBAL_ALLOWED_KEYS
PRESERVED_POINTERS: dict[str, frozenset[str]] = {}


class SkillSecretError(ValueError):
    """A compiled secret authority failed closed."""


def parse_secret_env_text(text: str, *, source: str = "<skill-secrets>") -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if raw != line or "=" not in raw:
            raise SkillSecretError(f"{source}:{line_number}: malformed assignment")
        key, value = raw.split("=", 1)
        if not KEY_RE.fullmatch(key):
            raise SkillSecretError(f"{source}:{line_number}: invalid key")
        if key in values:
            raise SkillSecretError(f"{source}:{line_number}: duplicate key")
        if not value or value != value.strip() or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in value
        ):
            raise SkillSecretError(f"{source}:{line_number}: invalid value")
        values[key] = value
    return values


def _open_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in (None, *absolute.parts[1:]):
            if component is not None:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            information = os.fstat(descriptor)
            if (
                information.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(information.st_mode) & 0o022
            ):
                raise SkillSecretError(
                    "credential authority ancestor is not owner-controlled"
                )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def read_protected_secret_env(path_value: str) -> str:
    supplied = Path(path_value)
    if not supplied.is_absolute() or path_value != path_value.strip():
        raise SkillSecretError("compiled secret authority is not an absolute path")
    absolute = Path(os.path.abspath(supplied))
    parent_descriptor = _open_directory_nofollow(absolute.parent)
    descriptor: int | None = None
    try:
        named = os.stat(absolute.name, dir_fd=parent_descriptor, follow_symlinks=False)
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_size > MAX_SECRET_FILE_BYTES
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise SkillSecretError("compiled secret authority is unsafe")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise SkillSecretError("compiled secret authority was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        named_after = os.stat(
            absolute.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or (after.st_dev, after.st_ino) != (
            named_after.st_dev,
            named_after.st_ino,
        ):
            raise SkillSecretError("compiled secret authority changed while reading")
    except OSError as exc:
        raise SkillSecretError(f"could not securely load compiled secrets: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillSecretError("compiled secret authority must be UTF-8") from exc


def _parse_secret_payload(text: str, *, source: str) -> dict[str, str]:
    if not text.lstrip().startswith("{"):
        return parse_secret_env_text(text, source=source)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillSecretError("compiled JSON secret authority is invalid") from exc
    if not isinstance(payload, dict):
        raise SkillSecretError("compiled JSON secret authority must contain an object")
    values: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise SkillSecretError("compiled JSON secret authority has an invalid key")
        if isinstance(value, (dict, list)):
            value = json.dumps(value, separators=(",", ":"), sort_keys=True)
        if not isinstance(value, str) or not value or any(ord(c) < 0x20 for c in value):
            raise SkillSecretError("compiled JSON secret authority has an invalid value")
        values[key] = value
    return values


def _profile_authority(profile: Profile, workspace: Path, home: Path) -> Path:
    scope, separator, relative = profile.authority.partition(":")
    if separator != ":":
        raise SkillSecretError("compiled secret authority is malformed")
    root = workspace if scope == "workspace" else home if scope == "home" else None
    if root is None:
        raise SkillSecretError("compiled secret authority scope is invalid")
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillSecretError("compiled secret authority path is invalid")
    return root.joinpath(*path.parts)


def _load_profile(profile: Profile, workspace: Path, home: Path) -> dict[str, str]:
    authority = _profile_authority(profile, workspace, home)
    if not os.path.lexists(authority):
        return {}
    payload = read_protected_secret_env(os.fspath(authority))
    if profile.json_only and not payload.lstrip().startswith("{"):
        raise SkillSecretError("this profile requires a JSON secret authority")
    values = _parse_secret_payload(payload, source=os.fspath(authority))
    if set(values) - profile.keys:
        raise SkillSecretError("compiled secret authority contains out-of-scope keys")
    return {key: values[key] for key in profile.keys if key in values}


def load_skill_secret_env(
    environ: Mapping[str, str] | None = None,
    *,
    selector_name: str,
    allowed_keys: frozenset[str] | None = None,
) -> dict[str, str]:
    """Compatibility API with compiled selector/key authority only.

    This import API intentionally ignores caller selector values.  It exists for
    unit-level consumers; the CLI uses named profiles exclusively.
    """

    del environ
    matching = [profile for profile in PROFILES.values() if profile.selector == selector_name]
    if not matching:
        raise SkillSecretError("unknown compiled secret selector")
    selected = matching[0].keys if allowed_keys is None else allowed_keys
    if not selected.issubset(matching[0].keys):
        raise SkillSecretError("secret projection exceeds the compiled authority")
    workspace = Path(__file__).resolve().parent.parent
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    loaded = _load_profile(matching[0], workspace, home)
    return {key: value for key, value in loaded.items() if key in selected}


def _validated_directory(path: Path) -> str:
    descriptor = _open_directory_nofollow(path)
    try:
        information = os.fstat(descriptor)
        if information.st_uid not in {0, os.geteuid()} or stat.S_IMODE(information.st_mode) & 0o022:
            raise SkillSecretError("compiled Python import root is not owner-controlled")
    finally:
        os.close(descriptor)
    return os.fspath(path)


def _import_roots(profile: Profile, workspace: Path, home: Path) -> list[str]:
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    roots: list[Path] = []
    for value in profile.import_roots:
        scope, separator, relative = value.partition(":")
        if separator != ":":
            raise SkillSecretError("compiled Python import root is malformed")
        if scope == "workspace":
            roots.append(workspace / relative.format(python=version))
        elif scope == "lean-closure" and workspace == Path("/workspace"):
            roots.append(Path("/opt/coding-system/python-closure/lean-explore/lib/python3.12/site-packages"))
        elif scope == "lean-closure":
            roots.append(
                home
                / ".local/share/coding-system/python-closure/lean-explore/lib/python3.12/site-packages"
            )
        else:
            raise SkillSecretError("compiled Python import root scope is invalid")
    return [_validated_directory(root) for root in roots if root.is_dir()]


def _safe_child_environment(loaded: Mapping[str, str], workspace: Path, home: Path) -> dict[str, str]:
    child = {name: os.environ[name] for name in SAFE_INHERITED_ENV if os.environ.get(name)}
    child.update(loaded)
    child["HOME"] = os.fspath(home)
    child["OPENCLAW_WORKSPACE"] = os.fspath(workspace)
    child["AAS_RUNTIME_WORKSPACE"] = os.fspath(workspace)
    child["PATH"] = SAFE_PATH
    return child


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("child", nargs=argparse.REMAINDER)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    child = list(args.child)
    if child and child[0] == "--":
        child = child[1:]
    try:
        profile = PROFILES[args.profile]
        skills_root = Path(__file__).resolve().parent
        workspace = skills_root.parent
        home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        script = skills_root.joinpath(*PurePosixPath(profile.script).parts)
        if script.is_symlink() or not script.is_file():
            raise SkillSecretError("compiled Python skill script is unavailable")
        loaded = _load_profile(profile, workspace, home)
        child_environment = _safe_child_environment(loaded, workspace, home)
        import_roots = _import_roots(profile, workspace, home)
        os.environ.clear()
        os.environ.update(child_environment)
        sys.path[:] = [os.fspath(script.parent), *import_roots, *sys.path]
        sys.argv = [os.fspath(script), *child]
        runpy.run_path(os.fspath(script), run_name="__main__")
        return 0
    except SkillSecretError as exc:
        print(f"skill secret load failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"skill launch failed: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ALLOWED_KEYS",
    "FORBIDDEN_SHARED_SELECTORS",
    "GLOBAL_ALLOWED_KEYS",
    "GLOBAL_SECRET_SELECTORS",
    "MAX_SECRET_FILE_BYTES",
    "PRESERVED_POINTERS",
    "PROFILES",
    "SECRET_PROJECTIONS",
    "SkillSecretError",
    "load_skill_secret_env",
    "parse_secret_env_text",
    "read_protected_secret_env",
]
