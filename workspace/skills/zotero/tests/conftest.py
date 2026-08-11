"""Shared test configuration and fixtures."""

import os
import sys
import json
from pathlib import Path
import importlib.util
import socket
import zipfile
import pytest

# Load the production package under a test-only unique name. Importing it as the
# generic top-level ``lib`` polluted collection for other independently tested
# skills (notably Calibre's own ``lib`` package).
SKILL_ROOT = Path(__file__).resolve().parent.parent
LIB_ROOT = SKILL_ROOT / "lib"
LIB_SPEC = importlib.util.spec_from_file_location(
    "zotero_test_lib",
    LIB_ROOT / "__init__.py",
    submodule_search_locations=[str(LIB_ROOT)],
)
if LIB_SPEC is None or LIB_SPEC.loader is None:
    raise RuntimeError("could not load the Zotero test package")
LIB_MODULE = importlib.util.module_from_spec(LIB_SPEC)
sys.modules["zotero_test_lib"] = LIB_MODULE
LIB_SPEC.loader.exec_module(LIB_MODULE)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="Run live integration tests")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test as requiring live credentials and network")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        skip_live = pytest.mark.skip(reason="Need --live flag to run")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def isolated_credentials_and_network(monkeypatch, tmp_path, request):
    """Prevent host selectors, credentials, and sockets from entering tests."""

    exact = {
        "AAS_SECRETS_FILE",
        "OPENCLAW_SECRETS_FILE",
        "AAS_SKILL_SECRETS_FILE",
        "AAS_ZOTERO_SKILL_SECRETS_FILE",
        "AAS_FILE_DELIVERY_SECRETS_FILE",
        "ZOTERO_API_KEY",
        "WEBDAV_PASSWORD",
        "GDRIVE_CREDENTIALS",
        "SEMANTIC_SCHOLAR_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "SMTP_PASSWORD",
        "SMTP_TOKEN",
    }
    sensitive_fragments = (
        "_API_KEY",
        "_PASSWORD",
        "_SECRET",
        "_TOKEN",
    )
    for key in list(os.environ):
        if key in exact or key.startswith(("AWS_", "ZOTERO_", "WEBDAV_", "GDRIVE_")) or any(
            fragment in key for fragment in sensitive_fragments
        ):
            monkeypatch.delenv(key, raising=False)

    private_home = tmp_path / "home"
    private_home.mkdir(mode=0o700)
    authorities = private_home / ".config" / "ai-agents-skills"
    authorities.mkdir(parents=True, mode=0o700)
    zotero_secrets = authorities / "zotero-secrets.json"
    zotero_secrets.write_text("{}\n", encoding="utf-8")
    zotero_secrets.chmod(0o600)
    delivery = private_home / ".config" / "file-delivery"
    delivery.mkdir(parents=True, mode=0o700)
    delivery_secrets = delivery / "secrets.json"
    delivery_secrets.write_text("{}\n", encoding="utf-8")
    delivery_secrets.chmod(0o600)
    monkeypatch.setenv("HOME", str(private_home))
    monkeypatch.setenv("AAS_ZOTERO_SKILL_SECRETS_FILE", str(zotero_secrets))
    monkeypatch.setenv("AAS_FILE_DELIVERY_SECRETS_FILE", str(delivery_secrets))

    if not request.config.getoption("--live"):
        def blocked(*_args, **_kwargs):
            raise AssertionError("network access is disabled in Zotero tests")

        monkeypatch.setattr(socket, "create_connection", blocked)
        monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def binary_fixtures_dir(tmp_path_factory):
    """Build deterministic, non-secret PDF/ZIP fixtures outside the source tree."""

    from PyPDF2 import PdfWriter

    root = tmp_path_factory.mktemp("zotero-binary-fixtures")

    def write_pdf(name, *, pages, width=612, height=792):
        path = root / name
        writer = PdfWriter()
        for _index in range(pages):
            writer.add_blank_page(width=width, height=height)
        with path.open("wb") as stream:
            writer.write(stream)
            # The verifier deliberately rejects downloads below 50 KiB. PDF
            # readers permit trailing comments after EOF, so deterministic
            # padding keeps these structural fixtures small in source form.
            stream.write(b"\n%" + b"fixture-padding" * 5_000 + b"\n")
        return path

    valid = write_pdf("valid_paper.pdf", pages=10)
    write_pdf("stub_1page.pdf", pages=1)
    write_pdf("slides_landscape.pdf", pages=4, width=792, height=612)
    write_pdf("scanned_paper.pdf", pages=4)

    archive = root / "sample_webdav.zip"
    info = zipfile.ZipInfo("Valid_2024_Paper [Journal Article].pdf")
    info.date_time = (2024, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(info, valid.read_bytes())
    return root


@pytest.fixture
def test_config():
    """Config dict for testing — no real credentials."""
    return {
        "zotero_user_id": "000000",
        "ZOTERO_API_KEY": "fake_test_key",
        "translation_server": "http://localhost:1969",
        "zotfile_pattern": "{author}_{year}_{title}",
        "default_collection": "",
        "auto_catalog_threshold": 80,
        "cache_max_age_hours": 24,
        "workspace": "/tmp/zot_test_workspace",
        "staging_dir": "/tmp/zot_test_workspace/staging",
    }
