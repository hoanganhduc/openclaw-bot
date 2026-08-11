"""Configuration loader for the Calibre skill.

Credential values are projected into the environment by ``run_cal.sh``.  This
module deliberately has no secret-file discovery or shared-authority fallback.
"""

import os
import json
import sys

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", "{{ OPENCLAW_WORKSPACE }}")
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "gdrive_folder_id": "",
    "staging_dir": os.path.join(WORKSPACE, "data", "calibre", "staging"),
    "cache_path": os.path.join(WORKSPACE, "data", "calibre", "cache", "library.json"),
    "db_local_path": os.path.join(WORKSPACE, "data", "calibre", "cache", "metadata.db"),
    "cache_max_age_hours": 24,
    "default_send_channel": "telegram",
    "isbn_lookup_url": "https://openlibrary.org/api/books",
    "preferred_format": "epub",
    "max_search_results": 25,
    "gdrive_share_permission": "anyone_with_link",
}
PUBLIC_CONFIG_KEYS = frozenset(DEFAULTS)


def load_config(require=None):
    config = dict(DEFAULTS)

    # Load config.json from skill dir
    cfg_path = os.path.join(SKILL_DIR, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as stream:
            public_config = json.load(stream)
        if not isinstance(public_config, dict):
            raise ValueError("Calibre config.json must contain an object")
        unexpected = set(public_config) - PUBLIC_CONFIG_KEYS
        if unexpected:
            raise ValueError("Calibre config.json contains unsupported keys")
        config.update(public_config)

    # Environment variable overrides
    for env_key, cfg_key in [
        ("GDRIVE_CREDENTIALS", "GDRIVE_CREDENTIALS"),
        ("CALIBRE_GDRIVE_FOLDER_ID", "gdrive_folder_id"),
        ("CALIBRE_STAGING_DIR", "staging_dir"),
    ]:
        val = os.environ.get(env_key)
        if val:
            config[cfg_key] = val

    # Credentials are now held only in ``config``. Helper subprocesses must
    # never inherit the projected authority through the ambient environment.
    os.environ.pop("GDRIVE_CREDENTIALS", None)
    os.environ.pop("CALIBRE_GDRIVE_FOLDER_ID", None)

    # Ensure directories exist
    os.makedirs(config["staging_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(config["cache_path"]), exist_ok=True)

    # Validate required keys
    if require:
        missing = [k for k in require if not config.get(k)]
        if missing:
            print(json.dumps({
                "status": "error",
                "message": f"Missing required config: {', '.join(missing)}. "
                           f"Set public values in config.json and credentials "
                           f"through the dedicated Calibre projection.",
            }))
            sys.exit(1)

    return config
