#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

DEFAULT_OUT_DIR = Path(r"{{ OPENCLAW_WORKSPACE }}/reports/formal_skeletons")


def slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unnamed_claim"


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def render(payload: dict):
    claim_name = payload.get("claim_name") or payload.get("claim") or "unnamed_claim"
    statement = payload.get("statement") or "Prop"
    theorem_name = slugify(claim_name)
    imports = payload.get("imports") or []
    namespace = payload.get("namespace") or ""
    variables = payload.get("variables") or []

    header = []
    for imp in imports:
        header.append(f"import {imp}")
    if header:
        header.append("")
    if namespace:
        header.append(f"namespace {namespace}")
        header.append("")
    if variables:
        for line in variables:
            header.append(str(line))
        header.append("")

    body = [f"theorem {theorem_name} : {statement} := by", "  sorry", ""]
    if namespace:
        body.append(f"end {namespace}")
        body.append("")
    return theorem_name, "\n".join(header + body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--output-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args()

    payload = {}
    if args.input:
        p = Path(args.input)
        if p.exists():
            text = p.read_text(encoding="utf-8").strip()
            payload = json.loads(text) if text else {}

    theorem_name, skeleton = render(payload)
    out = Path(args.output_dir) / f"{theorem_name}.lean"
    atomic_write(out, skeleton)

    print(json.dumps({
        "ok": True,
        "theorem_name": theorem_name,
        "path": str(out),
        "preview": skeleton,
    }, indent=2))


if __name__ == "__main__":
    main()
