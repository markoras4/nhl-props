"""One-command TikTok setup: sign in once, then paste three GitHub secrets.

    py video/setup_tiktok.py

1. Asks for the app's Client key and Client secret (developers.tiktok.com -> Manage apps -> evenout -> Credentials).
   Typing is hidden; paste each one and press Enter.
2. Opens TikTok's consent page in your browser (sign in with the evenout TikTok account and allow).
3. Puts the three values on your clipboard one at a time - TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET,
   TIKTOK_REFRESH_TOKEN - and waits for "done" after each, so you can paste each into its GitHub "New secret" page.
   Nothing secret is ever printed on screen.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth_tiktok as at  # noqa: E402
import auth_youtube as ay  # noqa: E402  (clipboard helper)

NAMES = ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--only", choices=NAMES, help="hand over just this one value (e.g. to fix one GitHub secret)")
    a = ap.parse_args()

    key = getpass.getpass("Paste the TikTok Client key (hidden) and press Enter: ").strip()
    sec = getpass.getpass("Paste the TikTok Client secret (hidden) and press Enter: ").strip()
    if not key or not sec:
        print("Both are needed. developers.tiktok.com -> Manage apps -> evenout -> Credentials.")
        return 1

    rt = at.sign_in(key, sec, a.port)
    if not rt:
        return 1

    print("\nSigned in. Now the GitHub secret(s), one at a time.")
    items = [(n, v) for n, v in zip(NAMES, (key, sec, rt)) if not a.only or n == a.only]
    for i, (name, value) in enumerate(items, 1):
        if not ay.copy_to_clipboard(value):
            print("Could not use the clipboard on this computer.")
            return 1
        print(f"\n{i}/{len(items)}  {name} is on your clipboard now.")
        print(f"     In the GitHub tab for {name}: click the big box, press Ctrl+V, click the green button.")
        while input("     When that is done, type  done  and press Enter: ").strip().lower() != "done":
            print("     (waiting - type done when the GitHub page has saved it)")
    ay.copy_to_clipboard(" ")
    print("\nAll saved in GitHub. Clipboard cleared. You can close this tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
