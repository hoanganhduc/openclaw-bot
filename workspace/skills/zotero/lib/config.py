"""Config loader for the zot CLI with environment-first secret handling."""

import json
import os
import sys

REQUIRED_FOR_SEARCH = ["zotero_user_id"]
SECRETS_KEYS = [
    "ZOTERO_API_KEY",
    "WEBDAV_PASSWORD",
    "GDRIVE_CREDENTIALS",
    "SEMANTIC_SCHOLAR_API_KEY",
]

DEFAULT_CONFIG = {
    "translation_server": "http://host.docker.internal:1969",
    "gdrive_share_permission": "anyone_with_link",
    "auto_catalog_threshold": 80,
    "cache_max_age_hours": 24,
    "zotfile_pattern": "{author}_{year}_{title}",
}


def _find_config_path():
    workspace = os.environ.get("OPENCLAW_WORKSPACE", "{{ OPENCLAW_WORKSPACE }}")
    return os.path.join(workspace, "skills", "zotero", "config.json")


def _load_secrets():
    """Use only the wrapper's already-bounded environment projection."""
    secrets = {}
    for key in SECRETS_KEYS:
        val = os.environ.get(key)
        if val:
            secrets[key] = val

    return secrets


def _scrub_projected_secrets():
    """Remove consumed credentials before any helper subprocess can inherit them."""

    for key in SECRETS_KEYS:
        os.environ.pop(key, None)


def load_config(require=None):
    """Load config + secrets. Returns merged dict.

    Args:
        require: list of required config keys (beyond REQUIRED_FOR_SEARCH).
                 Raises SystemExit if any are missing.
    """
    config_path = _find_config_path()
    if not os.path.exists(config_path):
        print(json.dumps({
            "status": "error",
            "action": "config",
            "message": f"Config file not found: {config_path}",
            "code": "CONFIG_MISSING",
        }))
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    # This legacy field used to appear in the non-secret config example. Never
    # use a credential from a normally 0644 config file; the uppercase secret
    # authority below comes from the managed environment or private secrets file.
    config.pop("semantic_scholar_api_key", None)

    # Apply defaults for missing optional keys
    for key, default in DEFAULT_CONFIG.items():
        if key not in config or config[key] == "":
            config[key] = default

    # Merge secrets
    secrets = _load_secrets()
    _scrub_projected_secrets()
    config.update(secrets)

    # Validate required fields
    required = list(REQUIRED_FOR_SEARCH)
    if require:
        required.extend(require)

    missing = [k for k in required if not config.get(k)]
    if missing:
        print(json.dumps({
            "status": "error",
            "action": "config",
            "message": f"Missing required config: {', '.join(missing)}",
            "code": "CONFIG_MISSING",
        }))
        sys.exit(1)

    # Resolve workspace path
    config["workspace"] = os.environ.get(
        "OPENCLAW_WORKSPACE", "{{ OPENCLAW_WORKSPACE }}"
    )
    os.environ["PATH"] = ":".join(
        (
            os.path.join(config["workspace"], ".local", "venv_getscipapers", "bin"),
            os.path.join(config["workspace"], ".local", "bin"),
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
        )
    )
    config["staging_dir"] = os.path.join(
        config["workspace"], "data", "research", "zotero", "staging"
    )

    return config
