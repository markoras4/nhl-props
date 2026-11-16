"""Upload the day's short videos to YouTube (Shorts) and TikTok.

    py video/upload.py --manifest video/out/2026-11-16/manifest.json
    py video/upload.py --manifest video/out/2026-11-16/manifest.json --only youtube
    py video/upload.py --manifest video/out/2026-11-16/manifest.json --dry-run

The manifest is written by video/series.py; each item is one file for ONE platform. Uploads are remembered in
data/video_uploads.json ("<id>:<platform>" -> video_id/publish_id, url, uploaded_at), so running again never posts
the same video twice. A platform whose secrets are missing is skipped. The script exits 0 even when an upload fails
(it reports the failure), so one broken platform never blocks the other; only a missing/broken manifest exits 1.

Secrets come from the environment and are never printed:
  YouTube: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, optional YT_PRIVACY (public|unlisted|private, default public)
  TikTok:  TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN, optional TIKTOK_REFRESH_TOKEN_OUT (a file path:
           TikTok hands out a new refresh token on every refresh; it is written there, never to the log)

Docs this follows (checked 2026-11):
  YouTube resumable upload  https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
  videos.insert             https://developers.google.com/youtube/v3/docs/videos/insert
  thumbnails.set            https://developers.google.com/youtube/v3/docs/thumbnails/set
  Google token refresh      https://developers.google.com/identity/protocols/oauth2/native-app
  TikTok token refresh      https://developers.tiktok.com/docs/en/oauth-refresh-a-user-access-token
  TikTok creator info       https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info
  TikTok direct post init   https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
  TikTok chunk rules        https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
  TikTok post status        https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "video_uploads.json"
SITE_URL = "https://evenout.app"
PLATFORMS = ("youtube", "tiktok")
TIMEOUT = 60  # seconds for ordinary API calls

# --- YouTube endpoints ---
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YT_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
YT_THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"

# --- TikTok endpoints ---
TT_API = "https://open.tiktokapis.com"
TT_TOKEN_URL = TT_API + "/v2/oauth/token/"
TT_CREATOR_URL = TT_API + "/v2/post/publish/creator_info/query/"
TT_INIT_URL = TT_API + "/v2/post/publish/video/init/"
TT_STATUS_URL = TT_API + "/v2/post/publish/status/fetch/"

MB = 1024 * 1024
TT_SINGLE_MAX = 64 * MB   # a file up to 64 MB goes up as one chunk (chunks must be 5-64 MB; under 5 MB: whole file)
TT_CHUNK = 10 * MB        # bigger files: 10 MB chunks, the last one takes the leftover bytes (allowed up to 128 MB)


def log(msg: str) -> None:
    print(msg, flush=True)


class UploadError(Exception):
    pass


# ----------------------------------------------------------------------------------------------------------- ledger

def ledger_key(item: dict) -> str:
    return f"{item.get('id')}:{item.get('platform')}"


def load_ledger(path: Path = LEDGER) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        # never silently forget what was uploaded: a corrupt ledger could cause double posts
        raise UploadError(f"cannot read the upload ledger {path}: {e}") from e


def save_ledger(ledger: dict, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------------------------------------- manifest

def resolve_path(p: str | None, manifest_path: Path) -> Path | None:
    """Manifest paths are relative to the repo root; also try the working dir and the manifest's own folder."""
    if not p:
        return None
    cand = Path(p)
    if cand.is_absolute():
        return cand
    for base in (Path.cwd(), ROOT, manifest_path.parent):
        if (base / cand).exists():
            return base / cand
    if (manifest_path.parent / cand.name).exists():
        return manifest_path.parent / cand.name
    return ROOT / cand  # does not exist; the caller reports it


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def have_secrets(platform: str) -> bool:
    names = {"youtube": ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"),
             "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN")}[platform]
    return all(env(n) for n in names)


def _err_text(r: requests.Response) -> str:
    """Short error text from a response, for logs (API error bodies carry no secrets; tokens are only in requests)."""
    try:
        body = r.json()
        if isinstance(body, dict):
            body.pop("access_token", None)
            body.pop("refresh_token", None)
        txt = json.dumps(body)
    except ValueError:
        txt = r.text or ""
    return f"HTTP {r.status_code}: {txt[:500]}"


# ---------------------------------------------------------------------------------------------------------- YouTube

def yt_metadata(item: dict) -> dict:
    title = (item.get("title") or item.get("id") or "evenout NHL").replace("<", "").replace(">", "").strip()
    if len(title) > 100:
        title = title[:99].rstrip() + "…"
    desc = (item.get("description") or item.get("caption") or "").replace("<", "").replace(">", "")
    if "evenout.app" not in desc:
        desc = (desc.rstrip() + "\n\n" if desc.strip() else "") + SITE_URL
    while len(desc.encode("utf-8")) > 5000:  # YouTube description limit: 5000 bytes
        desc = desc[:-50]
    tags, total = [], 0
    for t in item.get("tags") or []:
        t = str(t).replace("<", "").replace(">", "").replace(",", " ").strip()
        if not t:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if tags else 0)  # YouTube counts quotes and commas, max 500
        if total + cost > 500:
            break
        tags.append(t)
        total += cost
    privacy = (env("YT_PRIVACY") or "public").lower()
    if privacy not in ("public", "unlisted", "private"):
        privacy = "public"
    snippet = {"title": title, "description": desc, "categoryId": "17"}  # 17 = Sports
    if tags:
        snippet["tags"] = tags
    return {"snippet": snippet, "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}


class YouTube:
    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self._token = None

    def token(self) -> str:
        if self._token:
            return self._token
        r = self.s.post(GOOGLE_TOKEN_URL, data={
            "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
            "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=TIMEOUT)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or "access_token" not in body:
            hint = ""
            if "invalid_grant" in r.text:
                hint = (" (the refresh token was revoked or expired - if the OAuth consent screen is still in "
                        "'Testing', Google expires it after 7 days; publish the app and run video/auth_youtube.py again)")
            raise UploadError("YouTube sign-in failed: " + _err_text(r) + hint)
        self._token = body["access_token"]
        return self._token

    def upload(self, item: dict, path: Path, cover: Path | None) -> dict:
        meta = yt_metadata(item)
        size = path.stat().st_size
        auth = {"Authorization": f"Bearer {self.token()}"}
        r = self.s.post(YT_UPLOAD_URL, headers={**auth, "Content-Type": "application/json; charset=UTF-8",
                                                "X-Upload-Content-Length": str(size),
                                                "X-Upload-Content-Type": "video/mp4"},
                        data=json.dumps(meta), timeout=TIMEOUT)
        if r.status_code != 200 or not r.headers.get("Location"):
            raise UploadError("YouTube upload could not start: " + _err_text(r))
        session_uri = r.headers["Location"]
        log(f"  youtube: upload session open, sending {size / MB:.1f} MB")

        video = self._send(session_uri, path, size, auth)
        vid = video.get("id")
        if not vid:
            raise UploadError("YouTube upload finished without a video id")
        got = (video.get("status") or {}).get("privacyStatus")
        want = meta["status"]["privacyStatus"]
        if got and got != want:
            log(f"  youtube: WARNING video is '{got}', not '{want}' - YouTube may lock API uploads from unverified "
                f"projects to private; see video/UPLOAD.md")
        if cover and cover.exists():
            self.thumbnail(vid, cover)
        return {"video_id": vid, "url": f"https://www.youtube.com/shorts/{vid}", "privacy": got or want}

    def _send(self, uri: str, path: Path, size: int, auth: dict) -> dict:
        """PUT the bytes; on a dropped connection or 5xx ask how much arrived and resume (exponential backoff)."""
        start, attempt = 0, 0
        while True:
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    headers = {**auth, "Content-Type": "video/mp4", "Content-Length": str(size - start)}
                    if start:
                        headers["Content-Range"] = f"bytes {start}-{size - 1}/{size}"
                    r = self.s.put(uri, headers=headers, data=f, timeout=600)
                if r.status_code in (200, 201):
                    return r.json()
                if r.status_code not in (308, 500, 502, 503, 504):
                    if r.status_code == 404:
                        raise UploadError("YouTube upload session expired (404)")
                    raise UploadError("YouTube upload failed: " + _err_text(r))
            except requests.RequestException as e:
                log(f"  youtube: connection problem ({type(e).__name__}), will resume")
            attempt += 1
            if attempt > 6:
                raise UploadError("YouTube upload kept failing; gave up after 6 resumes")
            time.sleep(min(2 ** attempt, 60))
            # ask the server how many bytes it has
            q = self.s.put(uri, headers={**auth, "Content-Length": "0", "Content-Range": f"bytes */{size}"},
                           timeout=TIMEOUT)
            if q.status_code in (200, 201):
                return q.json()
            if q.status_code == 308:
                rng = q.headers.get("Range")  # e.g. "bytes=0-999999"
                start = int(rng.split("-")[-1]) + 1 if rng else 0
                log(f"  youtube: resuming at byte {start} of {size}")
            elif q.status_code == 404:
                raise UploadError("YouTube upload session expired (404)")

    def thumbnail(self, video_id: str, cover: Path) -> None:
        ctype = "image/png" if cover.suffix.lower() == ".png" else "image/jpeg"
        try:
            r = self.s.post(YT_THUMB_URL, params={"videoId": video_id},
                            headers={"Authorization": f"Bearer {self.token()}", "Content-Type": ctype},
                            data=cover.read_bytes(), timeout=TIMEOUT)
            if r.status_code == 200:
                log("  youtube: thumbnail set")
            else:
                log("  youtube: thumbnail not set (ignored; custom thumbnails need a phone-verified channel) "
                    + _err_text(r))
        except (requests.RequestException, OSError) as e:
            log(f"  youtube: thumbnail not set (ignored): {type(e).__name__}")


# ----------------------------------------------------------------------------------------------------------- TikTok

def tt_chunks(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per TikTok's media transfer guide."""
    if size <= TT_SINGLE_MAX:
        return size, 1
    count = size // TT_CHUNK          # rounded down; the final chunk absorbs the trailing bytes
    return TT_CHUNK, count


def tt_ranges(size: int) -> list[tuple[int, int]]:
    chunk, count = tt_chunks(size)
    out = []
    for i in range(count):
        first = i * chunk
        last = size - 1 if i == count - 1 else first + chunk - 1
        out.append((first, last))
    return out


class TikTok:
    def __init__(self, session: requests.Session | None = None, poll_interval: float = 5.0, poll_timeout: float = 300.0):
        self.s = session or requests.Session()
        self._token = None
        self._creator = None
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def token(self) -> str:
        if self._token:
            return self._token
        old = env("TIKTOK_REFRESH_TOKEN")
        r = self.s.post(TT_TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
            "client_key": env("TIKTOK_CLIENT_KEY"), "client_secret": env("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token", "refresh_token": old}, timeout=TIMEOUT)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200 or "access_token" not in body:
            raise UploadError("TikTok sign-in failed: " + _err_text(r) +
                              " (if the refresh token was already used or is older than 180 days, run "
                              "video/auth_tiktok.py again and update the TIKTOK_REFRESH_TOKEN secret)")
        new = body.get("refresh_token")
        if new and new != old:
            log("  tiktok: WARNING TikTok issued a NEW refresh token; the old one stops working. The GitHub secret "
                "TIKTOK_REFRESH_TOKEN must be updated (see video/UPLOAD.md).")
            out = env("TIKTOK_REFRESH_TOKEN_OUT")
            if out:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text(new, encoding="utf-8")
                try:
                    os.chmod(out, 0o600)
                except OSError:
                    pass
                log("  tiktok: the new refresh token was written to the file named by TIKTOK_REFRESH_TOKEN_OUT")
        self._token = body["access_token"]
        return self._token

    def _post(self, url: str, payload: dict) -> dict:
        r = self.s.post(url, headers={"Authorization": f"Bearer {self.token()}",
                                      "Content-Type": "application/json; charset=UTF-8"},
                        data=json.dumps(payload), timeout=TIMEOUT)
        try:
            body = r.json()
        except ValueError:
            body = {}
        code = (body.get("error") or {}).get("code")
        if r.status_code != 200 or code not in (None, "ok"):
            raise UploadError(f"TikTok {url.rsplit('/v2/', 1)[-1]} failed: " + _err_text(r))
        return body.get("data") or {}

    def creator(self) -> dict:
        if self._creator is None:
            self._creator = self._post(TT_CREATOR_URL, {})
        return self._creator

    def privacy(self) -> str:
        opts = self.creator().get("privacy_level_options") or []
        if "PUBLIC_TO_EVERYONE" in opts:
            return "PUBLIC_TO_EVERYONE"
        if not opts:
            raise UploadError("TikTok returned no privacy options for this account")
        choice = opts[0]
        log(f"  tiktok: PUBLIC_TO_EVERYONE is not allowed for this account/app (options: {', '.join(opts)}); "
            f"posting as {choice}.")
        if choice == "SELF_ONLY":
            log("  tiktok: NOTE posts stay private (only you can see them) until the app passes TikTok's "
                "Content Posting API audit; see video/UPLOAD.md")
        return choice

    def upload(self, item: dict, path: Path, cover: Path | None = None) -> dict:
        size = path.stat().st_size
        creator = self.creator()
        privacy = self.privacy()
        caption = (item.get("caption") or item.get("title") or "").strip()
        if "evenout" not in caption.lower():
            caption = (caption + " evenout.app").strip()
        caption = caption[:2200]
        chunk, count = tt_chunks(size)
        post_info = {"title": caption, "privacy_level": privacy,
                     "disable_comment": bool(creator.get("comment_disabled")),
                     "disable_duet": bool(creator.get("duet_disabled")),
                     "disable_stitch": bool(creator.get("stitch_disabled"))}
        if item.get("cover_timestamp_ms") is not None:
            post_info["video_cover_timestamp_ms"] = int(item["cover_timestamp_ms"])
        data = self._post(TT_INIT_URL, {"post_info": post_info, "source_info": {
            "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}})
        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        if not publish_id or not upload_url:
            raise UploadError("TikTok init returned no publish_id/upload_url")
        log(f"  tiktok: sending {size / MB:.1f} MB in {count} chunk(s) as {privacy}")
        with open(path, "rb") as f:
            for i, (first, last) in enumerate(tt_ranges(size), 1):
                f.seek(first)
                part = f.read(last - first + 1)
                for attempt in range(4):
                    try:
                        r = self.s.put(upload_url, data=part, timeout=600, headers={
                            "Content-Type": "video/mp4", "Content-Length": str(len(part)),
                            "Content-Range": f"bytes {first}-{last}/{size}"})
                    except requests.RequestException as e:
                        log(f"  tiktok: chunk {i} connection problem ({type(e).__name__}), retrying")
                        time.sleep(2 ** attempt)
                        continue
                    if r.status_code in (200, 201, 206):
                        break
                    if r.status_code >= 500:
                        time.sleep(2 ** attempt)
                        continue
                    raise UploadError(f"TikTok chunk {i}/{count} rejected: " + _err_text(r))
                else:
                    raise UploadError(f"TikTok chunk {i}/{count} kept failing")
        result = {"publish_id": publish_id, "privacy": privacy}
        status = self.wait(publish_id)
        result["status"] = status.get("status")
        ids = status.get("publicaly_available_post_id") or []   # (sic) TikTok's spelling
        user = creator.get("creator_username")
        if ids and user:
            result["url"] = f"https://www.tiktok.com/@{user}/video/{ids[0]}"
        elif user:
            result["url"] = f"https://www.tiktok.com/@{user}"
        return result

    def wait(self, publish_id: str) -> dict:
        deadline = time.monotonic() + self.poll_timeout
        last = None
        while True:
            st = self._post(TT_STATUS_URL, {"publish_id": publish_id})
            s = st.get("status")
            if s != last:
                log(f"  tiktok: status {s}")
                last = s
            if s == "PUBLISH_COMPLETE":
                return st
            if s == "FAILED":
                raise UploadError(f"TikTok processing failed: {st.get('fail_reason') or 'unknown reason'}")
            if s == "SEND_TO_USER_INBOX":
                return st
            if time.monotonic() > deadline:
                # bytes are uploaded and TikTok is still processing; count it as uploaded so it is never re-posted
                log("  tiktok: still processing after the wait limit; recording it as uploaded (check the app)")
                return st
            time.sleep(self.poll_interval)


# ------------------------------------------------------------------------------------------------------------- main

def run(manifest_path: Path, only: str | None = None, dry_run: bool = False, ledger_path: Path = LEDGER,
        clients: dict | None = None) -> dict:
    """Upload every item; returns {"uploaded": [...], "skipped": [...], "failed": [...]}."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("items") or []
    ledger = load_ledger(ledger_path)
    clients = clients or {}
    report = {"uploaded": [], "skipped": [], "failed": []}
    disabled: dict[str, str] = {}  # platform -> reason (no secrets, sign-in failed)
    log(f"Manifest {manifest_path} ({manifest.get('date', '?')}): {len(items)} item(s)"
        + (" [DRY RUN]" if dry_run else ""))

    for item in items:
        key = ledger_key(item)
        platform = (item.get("platform") or "").lower()
        if platform not in PLATFORMS:
            log(f"- {key}: unknown platform '{platform}', skipped")
            report["skipped"].append(key)
            continue
        if only and platform != only:
            continue
        if key in ledger:
            log(f"- {key}: already uploaded ({ledger[key].get('url') or ledger[key].get('video_id') or ledger[key].get('publish_id')}), skipped")
            report["skipped"].append(key)
            continue
        path = resolve_path(item.get("file"), manifest_path)
        if not path or not path.exists():
            log(f"- {key}: video file missing ({item.get('file')}), FAILED")
            report["failed"].append(key)
            continue
        cover = resolve_path(item.get("cover"), manifest_path)
        if platform in disabled:
            log(f"- {key}: skipped ({disabled[platform]})")
            report["skipped"].append(key)
            continue
        if not dry_run and not have_secrets(platform) and platform not in clients:
            disabled[platform] = f"no {platform} secrets set"
            log(f"- {key}: skipped ({disabled[platform]})")
            report["skipped"].append(key)
            continue

        size = path.stat().st_size / MB
        if dry_run:
            if platform == "youtube":
                m = yt_metadata(item)
                log(f"- {key}: would upload {path.name} ({size:.1f} MB) to YouTube as {m['status']['privacyStatus']}: "
                    f"\"{m['snippet']['title']}\", {len(m['snippet'].get('tags', []))} tags"
                    + (f", thumbnail {cover.name}" if cover and cover.exists() else ""))
            else:
                chunk, count = tt_chunks(path.stat().st_size)
                log(f"- {key}: would upload {path.name} ({size:.1f} MB, {count} chunk(s)) to TikTok, caption "
                    f"{len(item.get('caption') or item.get('title') or '')} chars")
            log(f"  secrets present: {'yes' if have_secrets(platform) else 'NO (would be skipped)'}")
            report["uploaded"].append(key)
            continue

        log(f"- {key}: uploading {path.name} ({size:.1f} MB)")
        try:
            if platform not in clients:
                clients[platform] = YouTube() if platform == "youtube" else TikTok()
            res = clients[platform].upload(item, path, cover)
        except UploadError as e:
            log(f"  {platform}: FAILED - {e}")
            report["failed"].append(key)
            if "sign-in failed" in str(e):
                disabled[platform] = f"{platform} sign-in failed earlier"
            continue
        except Exception as e:  # noqa: BLE001 - keep going with the other platform
            log(f"  {platform}: FAILED - unexpected {type(e).__name__}: {e}")
            report["failed"].append(key)
            continue
        res["uploaded_at"] = now_iso()
        res["file"] = item.get("file")
        ledger[key] = res
        save_ledger(ledger, ledger_path)
        log(f"  {platform}: done {res.get('url') or res.get('video_id') or res.get('publish_id')}")
        report["uploaded"].append(key)

    log(f"Summary: {len(report['uploaded'])} {'would upload' if dry_run else 'uploaded'}, "
        f"{len(report['skipped'])} skipped, {len(report['failed'])} failed")
    for k in report["failed"]:
        log(f"::warning::Video upload failed: {k}" if os.environ.get("GITHUB_ACTIONS") else f"  failed: {k}")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Upload the day's videos to YouTube and TikTok")
    ap.add_argument("--manifest", required=True, help="video/out/<DATE>/manifest.json")
    ap.add_argument("--only", choices=PLATFORMS, help="upload to one platform only")
    ap.add_argument("--dry-run", action="store_true", help="check files and show what would happen; no network")
    ap.add_argument("--ledger", default=str(LEDGER), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    mp = Path(a.manifest)
    if not mp.exists():
        log(f"Manifest not found: {mp}")
        return 1
    try:
        run(mp, only=a.only, dry_run=a.dry_run, ledger_path=Path(a.ledger))
    except (ValueError, UploadError) as e:
        log(f"Cannot run the uploads: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
