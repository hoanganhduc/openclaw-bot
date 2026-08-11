#!/usr/bin/env python3
"""OpenClaw helper for VNU eOffice chat and cron workflows."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(os.environ.get(
    "VNU_EOFFICE_REPO",
    "{{ OPENCLAW_WORKSPACE }}/vnueoffice_repo",
))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
DELIVERY_WORKSPACE = Path(os.environ.get(
    "OPENCLAW_WORKSPACE",
    "{{ OPENCLAW_WORKSPACE }}",
))
DATA_ROOT = Path(os.environ.get(
    "VNU_OPENCLAW_DATA_DIR",
    str(OPENCLAW_HOME / "workspace" / "data" / "vnu_eoffice"),
))
os.environ.setdefault("VNU_DATA_DIR", str(DATA_ROOT / "runtime"))
os.environ.setdefault("VNU_DOCS_DIR", str(DATA_ROOT / "documents"))
os.environ.setdefault("VNU_ITEMS_FILE", str(DATA_ROOT / "state" / "last_items.json"))

from vnu_eoffice import config  # noqa: E402
from vnu_eoffice.client import VnuClient  # noqa: E402
from vnu_eoffice.documents import (  # noqa: E402
    DocumentRef,
    download_documents,
    fetch_documents,
    search_documents,
)
from vnu_eoffice.items import (  # noqa: E402
    format_download_summary,
    format_listing,
    format_mapping_listing,
    format_monitor_result,
    load_mapping,
    resolve_document_refs,
    save_mapping,
    top_by_module,
    top_latest,
)
from vnu_eoffice.models import Document  # noqa: E402
from vnu_eoffice.monitor import delete_files, run_once, save_seen  # noqa: E402

config.DATA_DIR = Path(os.environ["VNU_DATA_DIR"])
config.STATE_DIR = config.DATA_DIR / "state"
config.DOCS_DIR = Path(os.environ["VNU_DOCS_DIR"])
config.SEEN_FILE = config.STATE_DIR / "seen.json"
config.ITEMS_FILE = Path(os.environ["VNU_ITEMS_FILE"])
if os.environ.get("VNU_SECRETS_FILE"):
    config.SECRETS_FILE = Path(os.environ["VNU_SECRETS_FILE"])

DEFAULT_MODULES = "den,di"
TITLE_MONITOR = "Cron task: VNU eOffice updates"
TITLE_LATEST = "Task: VNU eOffice latest documents"
TITLE_SEARCH = "Task: VNU eOffice document search"
TITLE_DELIVERY = "Task: VNU eOffice document delivery"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BrokenPipeError:
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_vnu_eoffice.sh",
        description="OpenClaw helper for VNU eOffice monitor, search, latest, and policy-gated file delivery.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check local configuration without printing secrets.")
    doctor.add_argument("--network", action="store_true", help="Also attempt login and one document-list request.")
    doctor.set_defaults(func=cmd_doctor)

    monitor = sub.add_parser("monitor", help="Run one monitor pass.")
    add_modules_arg(monitor)
    monitor.add_argument("--limit", type=positive_int, default=60)
    monitor.add_argument("--pages", type=positive_int, default=config.DEFAULT_FETCH_PAGES)
    monitor.add_argument("--download", action="store_true", help="Download alert attachments locally.")
    monitor.add_argument("--send-files", action="store_true", help="Queue alert attachments for host delivery.")
    add_delivery_args(monitor, include_send_flag=False)
    monitor.add_argument("--delete-after", action="store_true", help="Delete alert attachments after sending.")
    monitor.add_argument("--no-notify", action="store_true", help="Do not send monitor alerts.")
    monitor.add_argument("--dry-run", action="store_true", help="Do not write state or send Telegram messages.")
    monitor.set_defaults(func=cmd_monitor)

    latest = sub.add_parser("latest", help="Login and list top K latest documents.")
    add_modules_arg(latest)
    latest.add_argument("--limit", type=positive_int, default=10)
    latest.add_argument("--pages", type=positive_int, default=config.DEFAULT_FETCH_PAGES)
    latest.set_defaults(func=cmd_latest)

    search = sub.add_parser("search", help="Search documents by keyword.")
    add_modules_arg(search)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=positive_int, default=10)
    search.add_argument("--pages", type=positive_int, default=config.DEFAULT_FETCH_PAGES)
    search.add_argument("--has-attach", action="store_true")
    search.add_argument("--download-results", action="store_true")
    search.add_argument("--send-files", action="store_true", help="Queue downloaded results for host delivery.")
    add_delivery_args(search, include_send_flag=False)
    search.add_argument("--max-download", type=positive_int, default=5)
    search.add_argument("--keep-local", action="store_true")
    search.add_argument("--lookup-limit", type=positive_int, default=200)
    search.set_defaults(func=cmd_search)

    items = sub.add_parser("items", help="Show saved numbered items from the last latest/search/monitor run.")
    items.add_argument("--source", choices=("any", "latest", "search", "monitor"), default="any")
    items.set_defaults(func=cmd_items)

    download = sub.add_parser("download", help="Download and send documents by saved item number or direct ref.")
    download.add_argument("--ref", action="append", default=[], help="Document ref like den:123, di:456, or 123.")
    download.add_argument("--item", action="append", default=[], help="Saved item index, comma-list, or range.")
    download.add_argument("--all", action="store_true", help="Use every saved item from the latest listing/search.")
    download.add_argument("--source", choices=("any", "latest", "search", "monitor"), default="any")
    download.add_argument("--default-module", choices=tuple(config.MODULES), default="den")
    download.add_argument("--lookup-limit", type=positive_int, default=200)
    download.add_argument("--keep-local", action="store_true")
    download.add_argument("--send-files", action="store_true", help="Queue downloaded files for host delivery.")
    download.add_argument("--no-send", action="store_true", help=argparse.SUPPRESS)
    add_delivery_args(download, include_send_flag=False)
    download.set_defaults(func=cmd_download)

    return parser


def add_modules_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--modules", default=DEFAULT_MODULES, help="Comma-separated module list: den,di.")


def add_delivery_args(parser: argparse.ArgumentParser, *, include_send_flag: bool) -> None:
    if include_send_flag:
        parser.add_argument("--send-files", action="store_true")
    parser.add_argument(
        "--delivery-channel",
        choices=("telegram", "zulip", "googlechat", "whatsapp", "zalo"),
        default="telegram",
        help="Conversation channel for the host file-delivery queue.",
    )
    parser.add_argument(
        "--delivery-target",
        help="Exact conversation target; the host policy must already authorize it.",
    )


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def parse_modules(raw: str) -> tuple[str, ...]:
    modules = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not modules:
        raise ValueError("At least one module is required.")
    invalid = [module for module in modules if module not in config.MODULES]
    if invalid:
        raise ValueError(f"Unknown module(s): {', '.join(invalid)}")
    return modules


def cmd_doctor(args: argparse.Namespace) -> int:
    lines = [
        "VNU eOffice OpenClaw helper",
        f"package checkout: {REPO_ROOT}",
        f"openclaw data: {DATA_ROOT}",
        f"credentials configured: {configured_credentials()}",
        "delivery authority: host queue only",
    ]
    if args.network:
        client = VnuClient().login()
        counts = []
        for module in config.DEFAULT_MODULES:
            total, docs = client.list_documents(module, limit=1)
            counts.append(f"{module}: total={total}, sample={len(docs)}")
        lines.append("network login/list: ok")
        lines.append("modules: " + "; ".join(counts))
    print("\n".join(lines))
    return 0


def configured_credentials() -> str:
    try:
        config.get_credentials()
        return "yes"
    except Exception:
        return "no"


def cmd_monitor(args: argparse.Namespace) -> int:
    modules = parse_modules(args.modules)
    _validate_delivery_args(args)
    result = run_once(
        modules=modules,
        limit=args.limit,
        pages=args.pages,
        download=args.download or args.send_files,
        send_files=False,
        delete_after=False,
        notify=False,
        dry_run=args.dry_run,
        notify_alerts=False,
        save_seen_state=False,
    )
    alert_docs = [alert.doc for alert in result.alerts]
    text = format_monitor_result(result, modules, TITLE_MONITOR)
    print(text)

    delivery_error = None
    if args.send_files and not args.dry_run:
        try:
            for alert in result.alerts:
                _deliver_paths(
                    alert.files,
                    channel=args.delivery_channel,
                    target=args.delivery_target,
                    caption=f"{alert.doc.symbol} - {alert.doc.subject[:120]}",
                )
        except Exception as exc:
            delivery_error = f"attachment delivery failed: {exc}"
            result.errors.append(delivery_error)

    if args.delete_after and delivery_error is None:
        delete_files(path for alert in result.alerts for path in alert.files)

    if not args.dry_run and delivery_error is None:
        save_seen(result.seen_state)
    if alert_docs and not args.dry_run and delivery_error is None:
        save_mapping("monitor", alert_docs, query="new monitor alerts", modules=modules)

    for error in result.errors:
        print(f"  ! {error}")
    return 0 if not result.errors else 1


def cmd_latest(args: argparse.Namespace) -> int:
    modules = parse_modules(args.modules)
    client = VnuClient().login()
    docs: list[Document] = []
    for module in modules:
        _, module_docs = fetch_documents(client, module, limit=args.limit, pages=args.pages)
        docs.extend(top_latest(module_docs, args.limit))
    save_mapping("latest", docs, query="", modules=modules)
    text = format_listing(
        TITLE_LATEST,
        f"Latest documents - showing up to {args.limit} per category; scanned {args.pages} page(s).",
        docs,
        modules,
    )
    print(text)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    modules = parse_modules(args.modules)
    client = VnuClient().login()
    docs = search_documents(
        client,
        args.query,
        modules=modules,
        limit=args.limit,
        pages=args.pages,
        unread_only=False,
        has_attach=args.has_attach,
    )
    docs = top_by_module(docs, modules, args.limit)
    save_mapping("search", docs, query=args.query, modules=modules)
    text = format_listing(
        TITLE_SEARCH,
        f"Search: {args.query}\nShowing up to {args.limit} per category; scanned {args.pages} page(s).",
        docs,
        modules,
    )
    print(text)
    if args.download_results:
        _validate_delivery_args(args)
        refs = [DocumentRef(doc.module, doc.intid) for doc in docs[:args.max_download]]
        if not refs:
            print("No matching documents to download.")
            return 0
        downloaded = download_documents(
            client,
            refs,
            dest_dir=config.DOCS_DIR,
            lookup_limit=args.lookup_limit,
        )
        delivered = False
        if args.send_files:
            _deliver_downloads(
                downloaded,
                channel=args.delivery_channel,
                target=args.delivery_target,
                caption=TITLE_DELIVERY,
            )
            delivered = True
            if not args.keep_local:
                delete_files(_download_paths(downloaded))
        print(
            format_download_summary(
                downloaded,
                sent=delivered,
                deleted=delivered and not args.keep_local,
                title=TITLE_DELIVERY,
            )
        )
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    if args.no_send and args.send_files:
        raise ValueError("--no-send and --send-files are mutually exclusive")
    _validate_delivery_args(args)
    refs = resolve_document_refs(
        ids=args.ref,
        items=args.item,
        all_items=args.all,
        source=args.source,
        default_module=args.default_module,
    )
    client = VnuClient().login()
    downloaded = download_documents(
        client,
        refs,
        dest_dir=config.DOCS_DIR,
        lookup_limit=args.lookup_limit,
    )
    delivered = False
    if args.send_files and not args.no_send:
        _deliver_downloads(
            downloaded,
            channel=args.delivery_channel,
            target=args.delivery_target,
            caption=TITLE_DELIVERY,
        )
        delivered = True
        if not args.keep_local:
            delete_files(_download_paths(downloaded))
    print(
        format_download_summary(
            downloaded,
            sent=delivered,
            deleted=delivered and not args.keep_local,
            title=TITLE_DELIVERY,
        )
    )
    return 0


def cmd_items(args: argparse.Namespace) -> int:
    payload = load_mapping(args.source)
    print(format_mapping_listing(payload, "Task: VNU eOffice saved item numbers"))
    return 0


def _validate_delivery_args(args: argparse.Namespace) -> None:
    send_files = bool(getattr(args, "send_files", False))
    target = getattr(args, "delivery_target", None)
    if send_files and not target:
        raise ValueError("--send-files requires an explicit --delivery-target")
    if target and not send_files:
        raise ValueError("--delivery-target requires --send-files")


def _download_paths(downloaded: object) -> list[Path]:
    records = downloaded if isinstance(downloaded, (list, tuple)) else [downloaded]
    paths: list[Path] = []
    for record in records:
        values = getattr(record, "files", None)
        if values is None and isinstance(record, dict):
            values = record.get("files")
        if values is None:
            value = getattr(record, "path", None)
            if value is None and isinstance(record, dict):
                value = record.get("path")
            values = [value] if value is not None else []
        if isinstance(values, (str, os.PathLike)):
            values = [values]
        for value in values or []:
            path = Path(value)
            if path.is_file():
                paths.append(path)
    if not paths:
        raise RuntimeError("VNU download returned no regular document files")
    return paths


def _deliver_downloads(
    downloaded: object, *, channel: str, target: str, caption: str
) -> None:
    _deliver_paths(
        _download_paths(downloaded),
        channel=channel,
        target=target,
        caption=caption,
    )


def _deliver_paths(
    paths: object, *, channel: str, target: str, caption: str
) -> None:
    if not isinstance(target, str) or not target or any(ch in target for ch in "\r\n\t"):
        raise ValueError("delivery target is invalid")
    sender = DELIVERY_WORKSPACE / "skills" / "zotero" / "send_file.sh"
    if sender.is_symlink() or not sender.is_file():
        raise RuntimeError("approved host delivery queue producer is unavailable")
    for path_value in paths:
        path = Path(path_value)
        result = subprocess.run(
            [
                "/usr/bin/bash",
                "-p",
                os.fspath(sender),
                channel,
                target,
                os.fspath(path),
                caption[:1024],
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env={
                "HOME": os.environ.get("HOME", "/workspace"),
                "OPENCLAW_WORKSPACE": os.environ.get("OPENCLAW_WORKSPACE", "/workspace"),
                "PATH": "/usr/bin:/bin",
            },
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("host delivery queue returned invalid output") from exc
        if result.returncode != 0 or not isinstance(payload, dict) or payload.get("status") != "ok":
            raise RuntimeError("host delivery queue did not confirm delivery")


if __name__ == "__main__":
    raise SystemExit(main())
