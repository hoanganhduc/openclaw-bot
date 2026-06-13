#!/usr/bin/env python3
"""OpenClaw helper for VNU eOffice chat and cron workflows."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Sequence

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

from vnu_eoffice import config  # noqa: E402
from vnu_eoffice.client import VnuClient  # noqa: E402
from vnu_eoffice.documents import (  # noqa: E402
    DocumentRef,
    download_documents,
    fetch_documents,
    parse_document_refs,
    search_documents,
    send_documents,
)
from vnu_eoffice.models import Document  # noqa: E402
from vnu_eoffice.monitor import run_once  # noqa: E402
from vnu_eoffice.notify import TelegramNotifier, esc, load_chat_id  # noqa: E402

STATE_DIR = DATA_ROOT / "state"
MAPPING_FILE = STATE_DIR / "last_items.json"
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
    monitor.add_argument("--min-level", choices=("LOW", "MEDIUM", "HIGH"), default="MEDIUM")
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
    notifier = titled_notifier(TITLE_MONITOR) if should_notify else None
    result = run_once(
        modules=modules,
        limit=args.limit,
        pages=args.pages,
        min_level=args.min_level,
        download=args.download or args.send_files,
        send_files=args.send_files,
        delete_after=args.delete_after,
        notify=should_notify,
        notifier=notifier,
        dry_run=args.dry_run,
    )
    alert_docs = [alert.doc for alert in result.alerts]
    if alert_docs and not args.dry_run:
        save_mapping("monitor", alert_docs, query="new monitor alerts", modules=modules)
    print(format_monitor_result(result, modules))
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
        print(format_download_summary(downloaded, sent=True, deleted=not args.keep_local))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    refs = selected_refs(args)
    client = VnuClient().login()
    if args.no_send:
        downloaded = download_documents(
            client,
            refs,
            dest_dir=config.DOCS_DIR,
            lookup_limit=args.lookup_limit,
        )
        print(format_download_summary(downloaded, sent=False, deleted=False))
        return 0

    downloaded = send_documents(
        client,
        titled_notifier(TITLE_DELIVERY),
        refs,
        delete_after=not args.keep_local,
        dest_dir=config.DOCS_DIR,
        lookup_limit=args.lookup_limit,
    )
    print(format_download_summary(downloaded, sent=True, deleted=not args.keep_local))
    return 0


def cmd_items(args: argparse.Namespace) -> int:
    payload = load_mapping(args.source)
    print(format_mapping_listing(payload))
    return 0


def top_latest(docs: Iterable[Document], limit: int) -> list[Document]:
    return sorted(docs, key=lambda doc: doc.date or "", reverse=True)[:limit]


def top_by_module(docs: Iterable[Document], modules: Sequence[str], limit: int) -> list[Document]:
    grouped = {module: [] for module in modules}
    for doc in docs:
        grouped.setdefault(doc.module, []).append(doc)
    out: list[Document] = []
    for module in modules:
        out.extend(top_latest(grouped.get(module, []), limit))
    return out


def save_mapping(source: str, docs: Sequence[Document], query: str, modules: Sequence[str]) -> None:
    payload = {
        "source": source,
        "query": query,
        "modules": list(modules),
        "created_at": int(time.time()),
        "items": [
            {
                "index": index,
                "module": doc.module,
                "intid": doc.intid,
                "key": doc.key,
                "date": doc.date,
                "number": doc.number,
                "symbol": doc.symbol,
                "subject": doc.subject,
                "party": doc.party,
                "has_attach": doc.has_attach,
            }
            for index, doc in enumerate(docs, start=1)
        ],
    }
    config.write_private_text(MAPPING_FILE, json.dumps(payload, ensure_ascii=False, indent=2))


def load_mapping(source: str = "any") -> dict:
    try:
        payload = json.loads(MAPPING_FILE.read_text())
    except FileNotFoundError as exc:
        raise RuntimeError("No saved latest/search items. Run latest or search first.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Saved item mapping is corrupt. Run latest or search again.") from exc
    if source != "any" and payload.get("source") != source:
        raise RuntimeError(f"Saved item mapping is from {payload.get('source')!r}, not {source!r}.")
    return payload


def format_mapping_listing(payload: dict) -> str:
    source = payload.get("source") or "unknown"
    query = payload.get("query") or ""
    modules = tuple(payload.get("modules") or config.DEFAULT_MODULES)
    items = payload.get("items") or []
    lines = ["Task: VNU eOffice saved item numbers", "", f"Source: {source}"]
    if query:
        lines.append(f"Query: {query}")
    if not items:
        lines.append("No saved items.")
        return "\n".join(lines)
    lines.append("Use these item numbers for follow-up downloads.")
    by_module: dict[str, list[dict]] = {module: [] for module in modules}
    for item in items:
        by_module.setdefault(str(item.get("module")), []).append(item)
    for module in modules:
        lines.append("")
        lines.append(module_heading(module))
        module_items = by_module.get(module, [])
        if not module_items:
            lines.append("   No saved items in this category.")
            continue
        for item in module_items:
            meta = format_meta(
                str(item.get("key") or f"{item.get('module')}:{item.get('intid')}"),
                str(item.get("date") or "-")[:10],
                str(item.get("symbol") or item.get("number") or "-"),
                bool(item.get("has_attach")),
            )
            lines.append(f"{item.get('index')}. {meta}")
            party = item.get("party")
            if party:
                lines.append(f"   Unit: {short(party, 120)}")
            lines.append(f"   Subject: {short(item.get('subject'), 220)}")
    return "\n".join(lines)


def selected_refs(args: argparse.Namespace) -> list[DocumentRef]:
    refs: list[DocumentRef] = []
    if args.ref:
        refs.extend(parse_document_refs(args.ref, default_module=args.default_module))
    if args.all or args.item:
        payload = load_mapping(args.source)
        items = payload.get("items") or []
        if args.all:
            chosen = items
        else:
            wanted = parse_indices(args.item)
            by_index = {int(item["index"]): item for item in items}
            missing = [idx for idx in wanted if idx not in by_index]
            if missing:
                raise RuntimeError(f"Saved item number(s) not found: {', '.join(map(str, missing))}")
            chosen = [by_index[idx] for idx in wanted]
        refs.extend(DocumentRef(str(item["module"]), str(item["intid"])) for item in chosen)
    refs = dedupe_refs(refs)
    if not refs:
        raise RuntimeError("No documents selected. Use --item, --all, or --ref.")
    return refs


def parse_indices(values: Sequence[str]) -> list[int]:
    indices: list[int] = []
    for raw in values:
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start_s, end_s = (piece.strip() for piece in part.split("-", 1))
                start, end = int(start_s), int(end_s)
                if start < 1 or end < start:
                    raise ValueError(f"Invalid item range: {part}")
                indices.extend(range(start, end + 1))
            else:
                value = int(part)
                if value < 1:
                    raise ValueError(f"Invalid item number: {part}")
                indices.append(value)
    return dedupe_ints(indices)


def dedupe_refs(refs: Iterable[DocumentRef]) -> list[DocumentRef]:
    seen: set[tuple[str, str]] = set()
    out: list[DocumentRef] = []
    for ref in refs:
        key = (ref.module, ref.intid)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def dedupe_ints(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def format_monitor_result(result, modules: Sequence[str]) -> str:
    lines = [
        TITLE_MONITOR,
        "",
        "Status",
    ]
    if result.first_run and result.baseline_modules:
        lines.extend([
            f"- Baseline recorded: {result.baseline_count} document(s) in view.",
            f"- Modules initialized: {', '.join(result.baseline_modules)}.",
            "- Alerts: none on first run.",
        ])
    else:
        lines.extend([
            f"- New documents: {result.new_count}",
            f"- Alerts: {len(result.alerts)}",
            f"- Errors: {len(result.errors)}",
        ])
        if result.baseline_modules:
            lines.append(f"- Newly baselined modules: {', '.join(result.baseline_modules)}")

    lines.append("")
    lines.append("Scan")
    for module in modules:
        lines.append(f"- {module_heading(module)}")

    if result.alerts:
        lines.append("")
        lines.append("Follow-up")
        lines.append("- Alert item numbers were saved. Ask for item numbers to download files.")
        lines.extend(format_numbered_documents(
            [alert.doc for alert in result.alerts],
            modules,
            scores=[alert.score.level for alert in result.alerts],
        ))
    else:
        lines.append("")
        lines.append("No alert items in this run.")

    if result.errors:
        lines.append("")
        lines.append("Errors")
        for error in result.errors:
            lines.append(f"- {error}")
    return "\n".join(lines)


def format_listing(title: str, summary: str, docs: Sequence[Document], modules: Sequence[str]) -> str:
    lines = [title, "", summary, "", "Scan"]
    for module in modules:
        lines.append(f"- {module_heading(module)}")
    if not docs:
        lines.append("")
        lines.append("No documents found.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Follow-up")
    lines.append("Use these item numbers for follow-up downloads.")
    lines.extend(format_numbered_documents(docs, modules))
    return "\n".join(lines)


def format_numbered_documents(
    docs: Sequence[Document],
    modules: Sequence[str],
    scores: Sequence[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    score_by_key = {doc.key: scores[index] for index, doc in enumerate(docs)} if scores else {}
    by_module: dict[str, list[tuple[int, Document]]] = {module: [] for module in modules}
    for index, doc in enumerate(docs, start=1):
        by_module.setdefault(doc.module, []).append((index, doc))
    for module in modules:
        lines.append("")
        lines.append(module_heading(module))
        items = by_module.get(module, [])
        if not items:
            lines.append("   No documents found in this category.")
            continue
        for index, doc in items:
            meta = format_meta(
                doc.key,
                doc.date_short or "-",
                doc.symbol or doc.number or "-",
                doc.has_attach,
            )
            if score_by_key.get(doc.key):
                meta = f"{meta} | Score: {score_by_key[doc.key]}"
            lines.append(f"{index}. {meta}")
            if doc.party:
                lines.append(f"   Unit: {short(doc.party, 120)}")
            lines.append(f"   Subject: {short(doc.subject, 220)}")
    return lines


def format_meta(key: str, date: str, symbol: str, has_attach: bool) -> str:
    files = "yes" if has_attach else "no"
    return f"ID: {key} | Date: {date or '-'} | Ref: {symbol or '-'} | Files: {files}"


def module_heading(module: str) -> str:
    label = config.MODULES.get(module, {}).get("label", module)
    english = "Incoming" if module == "den" else "Outgoing" if module == "di" else module
    return f"{english} ({module}) - {label}"


def format_download_summary(downloaded, sent: bool, deleted: bool) -> str:
    files_count = sum(len(item.files) for item in downloaded)
    action = "sent" if sent else "downloaded"
    lines = [
        TITLE_DELIVERY,
        "",
        "Status",
        f"- Documents {action}: {len(downloaded)}",
        f"- Files: {files_count}",
        f"- Local copies deleted: {'yes' if deleted else 'no'}",
    ]
    for item in downloaded:
        lines.append(f"- {item.doc.key} | files={len(item.files)} | {short(item.doc.subject, 160)}")
    return "\n".join(lines)


def short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def send_plain_text(text: str, title: str = TITLE_DEFAULT) -> None:
    notifier = titled_notifier(title)
    body = remove_leading_title(text, title)
    for chunk in split_text(body, MAX_TEXT):
        notifier.send_message(esc(chunk))


def remove_leading_title(text: str, title: str) -> str:
    text = str(text or "")
    if text.startswith(title):
        return text[len(title):].lstrip("\n")
    return text


def split_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines() or [""]:
        addition = len(line) + 1
        if current and current_len + addition > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            chunks.extend(line[i:i + limit] for i in range(0, len(line), limit))
            continue
        current.append(line)
        current_len += addition
    if current:
        chunks.append("\n".join(current))
    return chunks


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
