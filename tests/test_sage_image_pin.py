#!/usr/bin/env python3
import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workspace/scripts/job_queue_worker.sh"
DIGEST_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")


def worker_images() -> dict[str, str]:
    match = re.search(
        r'aarch64\|arm64\) SAGE_IMAGE="([^"]+)" ;; \*\) SAGE_IMAGE="([^"]+)" ;;',
        WORKER.read_text(encoding="utf-8"),
    )
    assert match is not None, "SAGE_IMAGE selection not found"
    return {"arm64": match.group(1), "default": match.group(2)}


class SageImagePinTests(unittest.TestCase):
    def test_worker_runs_the_digest_pinned_manifest_images(self) -> None:
        manifest = json.loads((ROOT / "REBUILD-MANIFEST.json").read_text(encoding="utf-8"))["openclaw"]
        images = worker_images()
        self.assertEqual(images["arm64"], manifest["sagemath_image_arm64"])
        self.assertEqual(images["default"], manifest["sagemath_image"])
        for image in images.values():
            self.assertRegex(image, DIGEST_REFERENCE)

    def test_container_from_another_image_is_not_current(self) -> None:
        # A container created from an earlier image must be recreated, or a new
        # pin would never reach a host that already runs the worker.
        function = re.search(
            r"^sage_container_is_current\(\) \{\n.*?^\}\n",
            WORKER.read_text(encoding="utf-8"),
            re.M | re.S,
        )
        assert function is not None, "sage_container_is_current not found"
        harness = function.group(0) + r"""
docker() {
  case "$3" in
    *.Mounts*) printf '%s | /workspace/data/job-queue | true\n' "$JOB_QUEUE" ;;
    *.Config.Env*) printf 'DOT_SAGE=/tmp/.sage\n' ;;
    *.Config.User*) printf '%s\n' "$SAGE_RUN_USER" ;;
    *.Config.Image*) printf '%s\n' "$CONTAINER_IMAGE" ;;
    *) return 1 ;;
  esac
}
SAGE_CONTAINER=sagemath-worker SAGE_RUN_USER=1000:1000 JOB_QUEUE=/queue
SAGE_IMAGE="example/sagemath@sha256:$(printf 'a%.0s' {1..64})"
CONTAINER_IMAGE="$SAGE_IMAGE"
sage_container_is_current && echo same=current || echo same=stale
CONTAINER_IMAGE=example/sagemath:10.8
sage_container_is_current && echo other=current || echo other=stale
"""
        result = subprocess.run(
            ["/usr/bin/bash", "-c", harness],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["same=current", "other=stale"])


if __name__ == "__main__":
    unittest.main()
