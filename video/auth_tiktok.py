"""One-time TikTok sign-in: prints the refresh token for the GitHub secret TIKTOK_REFRESH_TOKEN.

Run it on your own computer (not in GitHub Actions):

    $env:TIKTOK_CLIENT_KEY = "..."        # PowerShell; from developers.tiktok.com -> your app -> Credentials
    $env:TIKTOK_CLIENT_SECRET = "..."
    py video/auth_tiktok.py

Your browser opens TikTok's consent page; sign in with the TikTok account the videos should go to and allow access.
TikTok sends the browser back to http://localhost:<port>/callback/ (this exact address must be listed as a redirect URI
under the app's Login Kit "Desktop" settings), this script catches it, swaps the code for tokens and prints ONLY the
refresh token. See video/UPLOAD.md.

Docs:
  https://developers.tiktok.com/doc/login-kit-desktop         (authorize URL, localhost redirect, PKCE: HEX SHA-256)
  https://developers.tiktok.com/docs/en/oauth-get-a-user-access-token   (token exchange; code_verifier for desktop)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import string
import sys
import urllib.parse
import webbrowser

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auth_youtube import catch_redirect  # noqa: E402  (same tiny local web server)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES = "user.info.basic,video.publish"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    key, sec = os.environ.get("TIKTOK_CLIENT_KEY", "").strip(), os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
    if not key or not sec:
        print("Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET first (developers.tiktok.com -> your app). "
              "See video/UPLOAD.md.")
        return 1
    redirect = f"http://localhost:{a.port}/callback/"
    alphabet = string.ascii_letters + string.digits + "-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok desktop wants hex, not base64url
    state = secrets.token_urlsafe(24)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": key, "scope": SCOPES, "response_type": "code", "redirect_uri": redirect, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print(f"Redirect URI used: {redirect}  (must be registered in the TikTok app exactly like this)")
    print("Opening the TikTok consent page in your browser. If it does not open, paste this link into it:\n")
    print(url + "\n")
    webbrowser.open(url)
    got = catch_redirect(a.port, "/callback/")
    if got.get("state") != state or "code" not in got:
        print("Sign-in failed or was cancelled:", got.get("error_description") or got.get("error")
              or "no answer within 5 minutes / state mismatch")
        return 1
    r = requests.post(TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
        "client_key": key, "client_secret": sec, "code": got["code"],  # parse_qs already URL-decoded it
        "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier}, timeout=60)
    try:
        body = r.json()
    except ValueError:
        body = {}
    rt = body.get("refresh_token")
    if r.status_code != 200 or not rt:
        print(f"Token exchange failed (HTTP {r.status_code}): {body.get('error')} {body.get('error_description', '')}")
        return 1
    if "video.publish" not in body.get("scope", ""):
        print("WARNING: video.publish was not granted - check the app has the Content Posting API with Direct Post on.")
    print("\nYour TikTok refresh token (keep it secret; valid 180 days, and TikTok replaces it on every use):\n")
    print(rt)
    print("\nNext: GitHub -> markoras4/nhl-props -> Settings -> Secrets and variables -> Actions -> New repository "
          "secret\n  Name: TIKTOK_REFRESH_TOKEN   Value: the line above\n"
          "Also add TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET there if you have not yet. Then close this window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
