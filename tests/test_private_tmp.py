"""Hostile-environment tests for owner plaintext staging."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/private_tmp.py"


def load_helper():
    specification = importlib.util.spec_from_file_location(
        "openclaw_private_tmp_test", HELPER
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class PrivateTmpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_helper()

    def test_default_is_owner_private_tmpfs_and_ignores_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as attacker:
            unsafe = Path(attacker) / "redirect"
            unsafe.symlink_to("/tmp", target_is_directory=True)
            with mock.patch.dict(os.environ, {"TMPDIR": str(unsafe)}):
                staging = self.module.create()
            try:
                self.assertNotEqual(staging.parent, Path("/tmp"))
                self.assertNotEqual(staging.parent, unsafe)
                self.assertEqual(self.module._filesystem_type(staging), "tmpfs")
                self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
                self.assertEqual(staging.stat().st_uid, os.geteuid())
            finally:
                self.module.remove(staging)
            self.assertFalse(staging.exists())

    def test_run_user_unavailable_falls_back_to_private_dev_shm_child(self) -> None:
        original = self.module._validated_base

        def select_dev_shm(path: Path, *, shared_sticky: bool):
            if path == Path(f"/run/user/{os.geteuid()}"):
                raise self.module.PrivateTmpError("synthetic unavailable runtime")
            return original(path, shared_sticky=shared_sticky)

        with mock.patch.object(
            self.module, "_validated_base", side_effect=select_dev_shm
        ):
            staging = self.module.create()
        try:
            self.assertEqual(staging.parent, Path("/dev/shm"))
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
        finally:
            self.module.remove(staging)

    def test_non_tmpfs_requires_exact_acknowledgement(self) -> None:
        with mock.patch.object(
            self.module,
            "_validated_base",
            side_effect=self.module.PrivateTmpError("synthetic no tmpfs"),
        ):
            with self.assertRaisesRegex(
                self.module.PrivateTmpError, "exact acknowledgement"
            ):
                self.module.create()
            with self.assertRaisesRegex(
                self.module.PrivateTmpError, "exact acknowledgement"
            ):
                self.module.create(acknowledgement="yes")
            staging = self.module.create(
                acknowledgement=self.module.ACKNOWLEDGEMENT
            )
        try:
            self.assertEqual(
                staging.parent,
                Path.home() / ".local/state/openclaw-bot/private-tmp",
            )
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
        finally:
            self.module.remove(
                staging, acknowledgement=self.module.ACKNOWLEDGEMENT
            )

    def test_unsafe_and_symlink_bases_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unsafe = root / "unsafe"
            unsafe.mkdir(mode=0o770)
            with mock.patch.object(
                self.module, "_filesystem_type", return_value="tmpfs"
            ):
                with self.assertRaisesRegex(
                    self.module.PrivateTmpError, "permissions are unsafe"
                ):
                    self.module._validated_base(unsafe, shared_sticky=False)
            real = root / "real"
            real.mkdir(mode=0o700)
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(OSError):
                self.module._validated_base(link, shared_sticky=False)

    def test_remove_rejects_wrong_prefix_parent_and_mode(self) -> None:
        staging = self.module.create()
        wrong_prefix = staging.parent / "not-openclaw-staging"
        wrong_prefix.mkdir(mode=0o700)
        try:
            with self.assertRaisesRegex(
                self.module.PrivateTmpError, "unrecognized staging path"
            ):
                self.module.remove(wrong_prefix)
        finally:
            wrong_prefix.rmdir()

        with tempfile.TemporaryDirectory() as temporary:
            wrong_parent = Path(temporary) / (
                self.module.NAME_PREFIX + "wrong-parent"
            )
            wrong_parent.mkdir(mode=0o700)
            with self.assertRaisesRegex(
                self.module.PrivateTmpError, "outside an approved base"
            ):
                self.module.remove(wrong_parent)

        staging.chmod(0o750)
        with self.assertRaisesRegex(
            self.module.PrivateTmpError, "unsafe plaintext staging"
        ):
            self.module.remove(staging)
        staging.chmod(0o700)
        self.module.remove(staging)

    def test_secret_plaintext_is_removed_from_admitted_tmpfs(self) -> None:
        secret = "synthetic-owner-secret-never-persist"
        staging = self.module.create()
        payload = staging / "owner.tar.gz"
        try:
            payload.write_text(secret, encoding="utf-8")
            payload.chmod(0o600)
            self.assertEqual(self.module._filesystem_type(payload), "tmpfs")
            self.assertEqual(payload.read_text(encoding="utf-8"), secret)
        finally:
            self.module.remove(staging)
            self.assertFalse(staging.exists())

    def test_owner_shells_fail_closed_when_plaintext_cleanup_fails(self) -> None:
        for name in ("backup.sh", "restore.sh"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("plaintext staging cleanup failed", source)
            self.assertIn("trap - EXIT", source)
            self.assertNotIn(">/dev/null 2>&1 || true", source)


if __name__ == "__main__":
    unittest.main()
