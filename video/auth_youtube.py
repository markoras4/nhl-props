"""One-time YouTube sign-in: prints the refresh token for the GitHub secret YT_REFRESH_TOKEN.

Run it on your own computer (not in GitHub Actions):

    $env:YT_CLIENT_ID = "....apps.googleusercontent.com"     # PowerShell; from your Google Cloud "Desktop app" client
    $env:YT_CLIENT_SECRET = "..."
    py video/auth_youtube.py

Your browser opens Google's consent page; sign in with the account that owns the YouTube channel and allow access.
Google sends the browser back to http://127.0.0.1:<port>/callback, this script catches it, swaps the one-time code
for tokens and prints ONLY the refresh token. See video/UPLOAD.md.

Docs: https://developers.google.com/identity/protocols/oauth2/native-app (loopback redirect + PKCE S256, base64url)
Scope: youtube.upload is enough for videos.insert and thumbnails.set
(https://developers.google.com/youtube/v3/docs/thumbnails/set lists youtube.upload among the accepted scopes).
"""
from __future__ import annotations

import argparse
import getpass
import subprocess
import base64
import hashlib
import http.server
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def catch_redirect(port: int, path: str = "/callback", timeout: int = 300) -> dict:
    """Serve one request on 127.0.0.1:port and return its query parameters."""
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            if u.path.rstrip("/") != path.rstrip("/"):
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in result
            self.wfile.write(("<h2>" + ("Done - you can close this tab and go back to the terminal."
                                        if ok else "Sign-in did not complete - see the terminal.") + "</h2>").encode())

        def log_message(self, *a):  # keep the code out of the console
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    srv.timeout = timeout
    t = threading.Thread(target=lambda: [srv.handle_request() for _ in range(5) if not result])
    t.daemon = True
    t.start()
    t.join(timeout)
    srv.server_close()
    return result


def copy_to_clipboard(text: str) -> bool:
    """Put text on the clipboard without printing it (Windows clip, macOS pbcopy, Linux xclip)."""
    for cmd, enc in ((["clip"], "utf-16le"), (["pbcopy"], "utf-8"), (["xclip", "-selection", "clipboard"], "utf-8")):
        try:
            subprocess.run(cmd, input=text.encode(enc), check=True)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--print", action="store_true", help="print the refresh token instead of copying it to the clipboard")
    a = ap.parse_args()
    cid, csec = os.environ.get("YT_CLIENT_ID", "").strip(), os.environ.get("YT_CLIENT_SECRET", "").strip()
    if not cid:
        cid = input("Paste the Client ID (Google Cloud -> Clients -> evenout uploader) and press Enter: ").strip()
    if not csec:
        csec = getpass.getpass("Paste the Client secret (stays hidden while you paste) and press Enter: ").strip()
    if not cid or not csec:
        print("Client ID and Client secret are both needed. See video/UPLOAD.md.")
        return 1
    redirect = f"http://127.0.0.1:{a.port}/callback"
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening the Google consent page in your browser. If it does not open, paste this link into it:\n")
    print(url + "\n")
    webbrowser.open(url)
    got = catch_redirect(a.port)
    if got.get("state") != state or "code" not in got:
        print("Sign-in failed or was cancelled:", got.get("error", "no answer within 5 minutes / state mismatch"))
        return 1
    r = requests.post(TOKEN_URL, data={"client_id": cid, "client_secret": csec, "code": got["code"],
                                       "code_verifier": verifier, "grant_type": "authorization_code",
                                       "redirect_uri": redirect}, timeout=60)
    body = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
    rt = body.get("refresh_token")
    if r.status_code != 200 or not rt:
        print(f"Token exchange failed (HTTP {r.status_code}): {body.get('error')} {body.get('error_description', '')}")
        return 1
    if SCOPE not in body.get("scope", ""):
        print("WARNING: the upload permission was not granted (tick the YouTube box on the consent page).")
    if not a.print and copy_to_clipboard(rt):
        print("\nDone. The refresh token is now on your clipboard (not shown here).\n"
              "Paste it (Ctrl+V) as the value of the GitHub secret YT_REFRESH_TOKEN, then close this tab.")
        return 0
    print("\nYour YouTube refresh token (keep it secret):\n")
    print(rt)
    print("\nNext: GitHub -> markoras4/nhl-props -> Settings -> Secrets and variables -> Actions -> New repository "
          "secret\n  Name: YT_REFRESH_TOKEN   Value: the line above\n"
          "Also add YT_CLIENT_ID and YT_CLIENT_SECRET there if you have not yet. Then close this window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
