#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

POLICY_DEFAULT = {
    "heavy_reasoning_keywords": [
        "proof",
        "theorem",
        "lemma",
        "corollary",
        "conjecture",
        "derive",
        "derivation",
        "formalize",
        "formalise",
        "math",
        "mathematics",
        "algebra",
        "topology",
        "combinatorics",
        "graph theory",
        "token sliding",
        "token jumping",
        "reconfiguration",
        "reduction",
        "np-hard",
        "nphard",
        "pspace",
        "complexity",
        "algorithm design",
        "approximation",
        "dynamic programming",
        "invariant",
        "counterexample",
        "debug proof",
        "logic",
        "correctness",
        "verification",
        "symbolic",
        "optimization proof",
        "mathematical",
    ],
    "premium_preferences": [
        "{{ MODEL_ID }}",
        "{{ MODEL_ID }}",
        "{{ MODEL_ID }}",
        "{{ MODEL_ID }}",
        "{{ MODEL_ID }}",
        "{{ MODEL_ID }}",
    ],
    "ordinary_timeout_seconds": 600,
    "heavy_timeout_seconds": 1800,
}

PROVIDER_MODEL_RE = re.compile(r"[a-z0-9][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:-]*")


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def run_cmd(args: Sequence[str], allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True)
    if not allow_fail and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(shlex.quote(a) for a in args)}\n{proc.stderr.strip()}"
        )
    return proc


def load_policy(base_dir: Path) -> Dict[str, Any]:
    path = base_dir / "router_policy.json"
    if not path.exists():
        return dict(POLICY_DEFAULT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(POLICY_DEFAULT)
    policy = dict(POLICY_DEFAULT)
    for key, value in data.items():
        policy[key] = value
    return policy


def normalize_model_name(s: str) -> str:
    return s.strip().strip('"\'')


def extract_models_from_json(blob: Any) -> List[str]:
    found: List[str] = []

    def visit(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(k, str) and PROVIDER_MODEL_RE.fullmatch(k):
                    found.append(k)
                if isinstance(v, str):
                    for m in PROVIDER_MODEL_RE.findall(v):
                        found.append(m)
                else:
                    visit(v)
        elif isinstance(x, list):
            for item in x:
                visit(item)
        elif isinstance(x, str):
            for m in PROVIDER_MODEL_RE.findall(x):
                found.append(m)

    visit(blob)
    deduped = []
    seen = set()
    for item in found:
        item = normalize_model_name(item)
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def get_models_status_json() -> Dict[str, Any] | None:
    proc = run_cmd(["openclaw", "models", "status", "--json"], allow_fail=True)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except Exception:
        return None


def get_models_list_json() -> Any:
    proc = run_cmd(["openclaw", "models", "list", "--json"], allow_fail=True)
    if proc.returncode == 0:
        try:
            return json.loads(proc.stdout)
        except Exception:
            return None
    return None


def get_current_primary(status: Dict[str, Any] | None) -> str | None:
    if not status:
        return None
    candidates = []

    def collect(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k in {"primary", "model", "resolvedPrimary"} and isinstance(v, str) and PROVIDER_MODEL_RE.fullmatch(v):
                    candidates.append(v)
                collect(v)
        elif isinstance(x, list):
            for item in x:
                collect(item)

    collect(status)
    return candidates[0] if candidates else None


def get_current_fallbacks(status: Dict[str, Any] | None) -> List[str]:
    if not status:
        return []
    found = []

    def collect(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "fallbacks" and isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and PROVIDER_MODEL_RE.fullmatch(item):
                            found.append(item)
                else:
                    collect(v)
        elif isinstance(x, list):
            for item in x:
                collect(item)

    collect(status)
    deduped = []
    seen = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def get_allowed_models(status: Dict[str, Any] | None, listing: Any) -> List[str]:
    found = []
    if status is not None:
        found.extend(extract_models_from_json(status))
    if listing is not None:
        found.extend(extract_models_from_json(listing))
    deduped = []
    seen = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def classify_task(task: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    text = task.lower()
    matches = [kw for kw in policy.get("heavy_reasoning_keywords", []) if kw.lower() in text]
    heavy = len(matches) > 0
    return {
        "class": "heavy_reasoning" if heavy else "default",
        "matched_keywords": matches,
    }


def choose_model(task: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    status = get_models_status_json()
    listing = get_models_list_json()
    allowed_models = get_allowed_models(status, listing)
    current_primary = get_current_primary(status)
    current_fallbacks = get_current_fallbacks(status)
    classification = classify_task(task, policy)

    preferred = None
    reason = []
    if classification["class"] == "heavy_reasoning":
        for candidate in policy.get("premium_preferences", []):
            if candidate in allowed_models:
                preferred = candidate
                reason.append(f"selected premium reasoning model {candidate}")
                break
        if preferred is None and current_primary in allowed_models:
            preferred = current_primary
            reason.append("no preferred premium model available; using current primary")
    else:
        if current_primary in allowed_models:
            preferred = current_primary
            reason.append("ordinary task; using current primary")
        elif allowed_models:
            preferred = allowed_models[0]
            reason.append("ordinary task; falling back to first configured model")

    if preferred is None:
        fail("Could not determine an allowed model from `openclaw models status/list`.")

    preferred_lower = preferred.lower()
    if classification["class"] == "heavy_reasoning":
        if ("codex" in preferred_lower) or preferred_lower == "{{ MODEL_ID }}":
            thinking = "xhigh"
        elif preferred_lower.startswith("{{ MODEL_ID }}") and ("4-6" in preferred_lower or "4.6" in preferred_lower):
            thinking = "high"
        else:
            thinking = "high"
        timeout = int(policy.get("heavy_timeout_seconds", 1800))
    else:
        thinking = "low"
        timeout = int(policy.get("ordinary_timeout_seconds", 600))

    return {
        "task": task,
        "classification": classification,
        "recommended_model": preferred,
        "recommended_thinking": thinking,
        "recommended_timeout_seconds": timeout,
        "current_primary": current_primary,
        "current_fallbacks": current_fallbacks,
        "allowed_models": allowed_models,
        "reason": reason,
    }


def print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_list_models(args: argparse.Namespace, _base_dir: Path) -> int:
    listing = get_models_list_json()
    status = get_models_status_json()
    payload = {
        "models_list": listing,
        "models_status": status,
    }
    if args.json:
        print_json(payload)
    else:
        if status is not None:
            print("Current model status:")
            print(json.dumps(status, indent=2, ensure_ascii=False))
        else:
            proc = run_cmd(["openclaw", "models", "status"], allow_fail=True)
            print(proc.stdout.rstrip())
            if proc.stderr.strip():
                print(proc.stderr.rstrip(), file=sys.stderr)
        print()
        if listing is not None:
            print("Available models:")
            print(json.dumps(listing, indent=2, ensure_ascii=False))
        else:
            proc = run_cmd(["openclaw", "models", "list"], allow_fail=True)
            print(proc.stdout.rstrip())
            if proc.stderr.strip():
                print(proc.stderr.rstrip(), file=sys.stderr)
    return 0


def cmd_doctor(args: argparse.Namespace, _base_dir: Path) -> int:
    models_cmd = ["openclaw", "models", "status", "--json", "--check"]
    if args.probe:
        models_cmd.append("--probe")
    models_proc = run_cmd(models_cmd, allow_fail=True)
    health_proc = run_cmd(["openclaw", "health", "--json"], allow_fail=True)
    payload = {
        "models_status_returncode": models_proc.returncode,
        "models_status": None,
        "models_status_stderr": models_proc.stderr,
        "health_returncode": health_proc.returncode,
        "health": None,
        "health_stderr": health_proc.stderr,
    }
    try:
        payload["models_status"] = json.loads(models_proc.stdout) if models_proc.stdout.strip() else None
    except Exception:
        payload["models_status"] = models_proc.stdout
    try:
        payload["health"] = json.loads(health_proc.stdout) if health_proc.stdout.strip() else None
    except Exception:
        payload["health"] = health_proc.stdout
    if args.json:
        print_json(payload)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if models_proc.returncode == 0 and health_proc.returncode == 0 else 1


def cmd_health(args: argparse.Namespace, _base_dir: Path) -> int:
    proc = run_cmd(["openclaw", "health", "--json"], allow_fail=True)
    if args.json or proc.returncode == 0:
        try:
            parsed = json.loads(proc.stdout) if proc.stdout.strip() else {}
            print_json(parsed)
        except Exception:
            sys.stdout.write(proc.stdout)
    else:
        sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def cmd_show_policy(_args: argparse.Namespace, base_dir: Path) -> int:
    policy = load_policy(base_dir)
    status = get_models_status_json()
    payload = {
        "policy": policy,
        "current_primary": get_current_primary(status),
        "current_fallbacks": get_current_fallbacks(status),
        "channel_override_hint": (
            "Channel-pinned models via channels.modelByChannel can override the global default when the session has no explicit /model override."
        ),
    }
    print_json(payload)
    return 0


def cmd_set_primary(args: argparse.Namespace, _base_dir: Path) -> int:
    proc = run_cmd(["openclaw", "models", "set", args.model], allow_fail=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def cmd_add_fallback(args: argparse.Namespace, _base_dir: Path) -> int:
    proc = run_cmd(["openclaw", "models", "fallbacks", "add", args.model], allow_fail=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def cmd_remove_fallback(args: argparse.Namespace, _base_dir: Path) -> int:
    proc = run_cmd(["openclaw", "models", "fallbacks", "remove", args.model], allow_fail=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def cmd_clear_fallbacks(_args: argparse.Namespace, _base_dir: Path) -> int:
    proc = run_cmd(["openclaw", "models", "fallbacks", "clear"], allow_fail=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


def cmd_recommend(args: argparse.Namespace, base_dir: Path) -> int:
    payload = choose_model(args.task, load_policy(base_dir))
    print_json(payload)
    return 0


def cmd_suggest_session_switch(args: argparse.Namespace, base_dir: Path) -> int:
    payload = choose_model(args.task, load_policy(base_dir))
    payload["suggested_command"] = f"/model {payload['recommended_model']}"
    print_json(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw smart model router helper")
    parser.add_argument("--base-dir", default=str(Path(__file__).resolve().parent), help="skill base directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list-models", help="show models list and resolved status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list_models)

    p = sub.add_parser("doctor", help="probe model/gateway health")
    p.add_argument("--probe", action="store_true", help="run live provider probes")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("health", help="fetch gateway health snapshot")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("show-policy", help="show router policy and current defaults")
    p.set_defaults(func=cmd_show_policy)

    p = sub.add_parser("set-primary", help="set the global primary model")
    p.add_argument("model")
    p.set_defaults(func=cmd_set_primary)

    p = sub.add_parser("add-fallback", help="add a fallback model")
    p.add_argument("model")
    p.set_defaults(func=cmd_add_fallback)

    p = sub.add_parser("remove-fallback", help="remove a fallback model")
    p.add_argument("model")
    p.set_defaults(func=cmd_remove_fallback)

    p = sub.add_parser("clear-fallbacks", help="clear all fallbacks")
    p.set_defaults(func=cmd_clear_fallbacks)

    p = sub.add_parser("recommend", help="recommend a model/thinking/timeout for a task")
    p.add_argument("task")
    p.set_defaults(func=cmd_recommend)

    p = sub.add_parser("suggest-session-switch", help="recommend a /model command for this session")
    p.add_argument("task")
    p.set_defaults(func=cmd_suggest_session_switch)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    return int(args.func(args, base_dir))


if __name__ == "__main__":
    raise SystemExit(main())
