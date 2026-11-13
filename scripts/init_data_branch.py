"""Create the `data` branch with config.json and state.json (idempotent).

Usage: GITHUB_TOKEN=... python scripts/init_data_branch.py --repo owner/name
Existing files are never overwritten. The token is never printed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tiyatro.config import config_to_dict, default_config  # noqa: E402
from tiyatro.state import empty_state  # noqa: E402
from tiyatro.store import Store, StoreError  # noqa: E402


def main(argv=None, store=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                    help="owner/name (default: env GITHUB_REPOSITORY)")
    args = ap.parse_args(argv)
    if store is None:
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token or not args.repo:
            print("need --repo and env GITHUB_TOKEN", file=sys.stderr)
            return 2
        store = Store(args.repo, token)
    try:
        store.ensure_branch()
        print("branch data: present")
        for path, data in (("config.json", config_to_dict(default_config())),
                           ("state.json", empty_state())):
            created = store.create_file(path, data)
            print(f"{path}: {'created' if created else 'already exists, left untouched'}")
    except (StoreError, RuntimeError) as exc:
        print(f"init failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
