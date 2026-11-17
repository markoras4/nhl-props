"""One-command YouTube setup: sign in once, then paste three GitHub secrets.

    py video/setup_youtube.py

1. Reads the newest client_secret_*.json from your Downloads folder (Google Cloud -> Clients -> evenout uploader ->
   Add secret -> Download JSON), or the file given with --json.
2. Opens Google's sign-in in your browser (choose the account that owns the YouTube channel and allow).
3. Puts the three values on your clipboard one at a time - YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN -
   and waits for Enter after each, so you can paste each one into its GitHub "New secret" page.
   Nothing secret is ever printed on screen.
4. Clears the clipboard and offers to delete the downloaded JSON file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_youtube as ay  # noqa: E402


def find_json(arg: str | None) -> Path | None:
    if arg:
        p = Path(arg).expanduser()
        return p if p.exists() else None
    files = sorted((Path.home() / "Downloads").glob("client_secret_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", help="path to the client_secret_*.json file (default: newest one in Downloads)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--only", choices=["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"],
                    help="hand over just this one value (e.g. to fix one GitHub secret)")
    a = ap.parse_args()

    path = find_json(a.json)
    if not path:
        print("No client_secret_*.json found in your Downloads folder.\n"
              "Google Cloud -> Clients -> evenout uploader -> Add secret -> Download JSON, then run this again.")
        return 1
    try:
        conf = json.loads(path.read_text(encoding="utf-8"))
        c = conf.get("installed") or conf.get("web") or {}
        cid, csec = c["client_id"], c["client_secret"]
    except Exception:  # noqa: BLE001
        print(f"Could not read the client ID and secret from {path.name}.")
        return 1
    print(f"Using {path.name}.\n")

    rt = ay.sign_in(cid, csec, a.port)
    if not rt:
        return 1

    print("\nSigned in. Now the GitHub secret(s), one at a time.")
    items = [(n, v) for n, v in (("YT_CLIENT_ID", cid), ("YT_CLIENT_SECRET", csec), ("YT_REFRESH_TOKEN", rt))
             if not a.only or n == a.only]
    for i, (name, value) in enumerate(items, 1):
        if not ay.copy_to_clipboard(value):
            print("Could not use the clipboard on this computer. Run: py video/auth_youtube.py --print")
            return 1
        print(f"\n{i}/{len(items)}  {name} is on your clipboard now.")
        print(f"     In the GitHub tab for {name}: click the big box, press Ctrl+V, click the green button.")
        while input("     When that is done, type  done  and press Enter: ").strip().lower() != "done":
            print("     (waiting - type done when the GitHub page has saved it)")
    ay.copy_to_clipboard(" ")
    print("\nAll saved in GitHub. Clipboard cleared.")
    ans = input(f"Delete {path.name} from Downloads now (recommended)? [Y/n] ").strip().lower()
    if ans in ("", "y", "yes"):
        try:
            path.unlink()
            print("Deleted.")
        except Exception:  # noqa: BLE001
            print("Could not delete it - please delete it yourself.")
    print("\nDone. You can close this tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
