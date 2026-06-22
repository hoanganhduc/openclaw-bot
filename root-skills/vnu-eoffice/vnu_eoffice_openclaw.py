#!/usr/bin/env python3
"""OpenClaw helper for VNU eOffice chat and cron workflows."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(os.environ.get("VNU_EOFFICE_REPO", "{{ USER_HOME }}/vnueoffice"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
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
    send_documents,
)
from vnu_eoffice.items import (  # noqa: E402
    format_download_summary,
    format_listing,
    format_mapping_listing,
    format_monitor_result,
    load_mapping,
    remove_leading_title,
    resolve_document_refs,
    save_mapping,
    split_text,
    top_by_module,
    top_latest,
)
from vnu_eoffice.models import Document  # noqa: E402
from vnu_eoffice.monitor import delete_files, run_once, save_seen  # noqa: E402
from vnu_eoffice.notify import TelegramNotifier, esc, load_chat_id  # noqa: E402

config.DATA_DIR = Path(os.environ["VNU_DATA_DIR"])
config.STATE_DIR = config.DATA_DIR / "state"
config.DOCS_DIR = Path(os.environ["VNU_DOCS_DIR"])
config.SEEN_FILE = config.STATE_DIR / "seen.json"
config.TELEGRAM_STATE_FILE = config.STATE_DIR / "telegram.json"
config.ITEMS_FILE = Path(os.environ["VNU_ITEMS_FILE"])
if os.environ.get("VNU_SECRETS_FILE"):
    config.SECRETS_FILE = Path(os.environ["VNU_SECRETS_FILE"])

DEFAULT_MODULES = "den,di"
MAX_TEXT = 3800
TITLE_DEFAULT = "Task: VNU eOffice"
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
        description="OpenClaw helper for VNU eOffice monitor, search, latest, and Telegram file sending.",
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
    monitor.add_argument("--send-files", action="store_true", help="Send alert attachments when --download is used.")
    monitor.add_argument("--delete-after", action="store_true", help="Delete alert attachments after sending.")
    monitor.add_argument("--no-notify", action="store_true", help="Do not send monitor alerts.")
    monitor.add_argument("--dry-run", action="store_true", help="Do not write state or send Telegram messages.")
    monitor.set_defaults(func=cmd_monitor)

    latest = sub.add_parser("latest", help="Login and list top K latest documents.")
    add_modules_arg(latest)
    latest.add_argument("--limit", type=positive_int, default=10)
    latest.add_argument("--pages", type=positive_int, default=config.DEFAULT_FETCH_PAGES)
    latest.add_argument("--send-telegram", action="store_true")
    latest.set_defaults(func=cmd_latest)

    search = sub.add_parser("search", help="Search documents by keyword.")
    add_modules_arg(search)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=positive_int, default=10)
    search.add_argument("--pages", type=positive_int, default=config.DEFAULT_FETCH_PAGES)
    search.add_argument("--has-attach", action="store_true")
    search.add_argument("--send-telegram", action="store_true")
    search.add_argument("--download-results", action="store_true")
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
    download.add_argument("--no-send", action="store_true", help="Download only; do not send via Telegram.")
    download.set_defaults(func=cmd_download)

    return parser


def add_modules_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--modules", default=DEFAULT_MODULES, help="Comma-separated module list: den,di.")


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
        f"telegram token configured: {'yes' if config.get_telegram_token() else 'no'}",
        f"telegram chat configured: {'yes' if load_chat_id() else 'no'}",
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
    should_notify = not args.no_notify and not args.dry_run
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
    should_send_summary = should_notify and (alert_docs or (result.first_run and result.baseline_modules))
    if should_send_summary:
        try:
            send_plain_text(text, TITLE_MONITOR)
            if args.send_files:
                notifier = titled_notifier(TITLE_MONITOR)
                for alert in result.alerts:
                    for path in alert.files:
                        notifier.send_document(path, caption=f"{alert.doc.symbol} - {alert.doc.subject[:120]}")
        except Exception as exc:
            delivery_error = f"summary delivery failed: {exc}"
            result.errors.append(delivery_error)

    if args.delete_after:
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
    if args.send_telegram:
        send_plain_text(text, TITLE_LATEST)
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
    if args.send_telegram:
        send_plain_text(text, TITLE_SEARCH)
    if args.download_results:
        refs = [DocumentRef(doc.module, doc.intid) for doc in docs[:args.max_download]]
        if not refs:
            print("No matching documents to download.")
            return 0
        downloaded = send_documents(
            client,
            titled_notifier(TITLE_DELIVERY),
            refs,
            delete_after=not args.keep_local,
            dest_dir=config.DOCS_DIR,
            lookup_limit=args.lookup_limit,
        )
        print(format_download_summary(downloaded, sent=True, deleted=not args.keep_local, title=TITLE_DELIVERY))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    refs = resolve_document_refs(
        ids=args.ref,
        items=args.item,
        all_items=args.all,
        source=args.source,
        default_module=args.default_module,
    )
    client = VnuClient().login()
    if args.no_send:
        downloaded = download_documents(
            client,
            refs,
            dest_dir=config.DOCS_DIR,
            lookup_limit=args.lookup_limit,
        )
        print(format_download_summary(downloaded, sent=False, deleted=False, title=TITLE_DELIVERY))
        return 0

    downloaded = send_documents(
        client,
        titled_notifier(TITLE_DELIVERY),
        refs,
        delete_after=not args.keep_local,
        dest_dir=config.DOCS_DIR,
        lookup_limit=args.lookup_limit,
    )
    print(format_download_summary(downloaded, sent=True, deleted=not args.keep_local, title=TITLE_DELIVERY))
    return 0


def cmd_items(args: argparse.Namespace) -> int:
    payload = load_mapping(args.source)
    print(format_mapping_listing(payload, "Task: VNU eOffice saved item numbers"))
    return 0


def send_plain_text(text: str, title: str = TITLE_DEFAULT) -> None:
    notifier = titled_notifier(title)
    body = remove_leading_title(text, title)
    for chunk in split_text(body, MAX_TEXT):
        notifier.send_message(esc(chunk))


def titled_notifier(title: str) -> "TaskTitleNotifier":
    return TaskTitleNotifier(TelegramNotifier.from_config(), title)


class TaskTitleNotifier:
    def __init__(self, notifier: TelegramNotifier, title: str):
        self.notifier = notifier
        self.title = title

    def send_message(self, text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> dict:
        return self.notifier.send_message(
            titled_message(self.title, text, parse_mode),
            parse_mode=parse_mode,
            disable_preview=disable_preview,
        )

    def send_document(self, path: Path, caption: str = "") -> dict:
        return self.notifier.send_document(path, caption=titled_caption(self.title, caption))


def titled_message(title: str, text: str, parse_mode: str = "HTML") -> str:
    if parse_mode.upper() == "HTML":
        return f"<b>{esc(title)}</b>\n{text}"
    return f"{title}\n{text}"


def titled_caption(title: str, caption: str) -> str:
    caption = str(caption or "")
    text = f"{title} - {caption}" if caption else title
    return text[:1024]


if __name__ == "__main__":
    raise SystemExit(main())
