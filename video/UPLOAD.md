# Automatic video uploads (YouTube Shorts + TikTok)

Twice a day (10:00 and 15:30 Swedish time) GitHub runs **Daily videos** (`.github/workflows/videos.yml`): it renders the day's short
videos (`video/series.py`: 10:00 the weekly report about past games, 15:30 the videos about tonight's games), keeps them as a download on the run page for 7 days, then posts them with
`video/upload.py`. What was posted is remembered in `data/video_uploads.json`, so a video is never posted twice,
even if you press "Run workflow" again. A platform without its secrets is simply skipped.

You do the setup below once. It takes about 30-45 minutes. You never need to send these keys to anyone: they go
straight into GitHub's secret store.

---

## 1. YouTube

1. Go to <https://console.cloud.google.com/> and create a project (e.g. "evenout videos").
2. **APIs & Services -> Library** -> search **YouTube Data API v3** -> **Enable**.
3. **APIs & Services -> OAuth consent screen** (now called "Google Auth Platform"):
   - User type **External**, app name "evenout", your e-mail as support and developer contact.
   - **Data access / Scopes**: add `https://www.googleapis.com/auth/youtube.upload`.
   - **Audience / Test users**: add the Google account that owns the YouTube channel.
   - **Important:** then press **Publish app** ("In production"). While the app is in *Testing*, Google lets the
     sign-in expire after **7 days** and uploads stop. Publishing does not need Google's review for your own use;
     you will just see a "Google hasn't verified this app" screen once - click *Advanced -> Go to evenout (unsafe)*.
4. **APIs & Services -> Credentials -> Create credentials -> OAuth client ID** -> type **Desktop app**.
   Copy the **Client ID** and **Client secret**.
5. On your computer, in PowerShell in the project folder:

   ```powershell
   $env:YT_CLIENT_ID = "paste client id"
   $env:YT_CLIENT_SECRET = "paste client secret"
   py video/auth_youtube.py
   ```

   The browser opens; choose the channel's Google account and allow. The terminal prints one long line: the
   refresh token.
6. GitHub -> `markoras4/nhl-props` -> **Settings -> Secrets and variables -> Actions -> New repository secret**, three times:
   `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
   Optional: under the **Variables** tab add `YT_PRIVACY` = `unlisted` or `private` to test first (default `public`).

**Limits to know**
- Quota: per Google's current docs (checked Nov 2026) an upload uses its own bucket of **100 uploads per day**
  (<https://developers.google.com/youtube/v3/determine_quota_cost>); setting the cover picture costs 50 of the
  10,000 daily units. (Older docs said an upload cost about 1,600 of the 10,000 units, i.e. ~6 uploads a day.)
  We post 1-3 videos a day, well inside either rule.
- Private lock: for years Google forced every API upload from an *unverified* project to **private** until the
  project passed the YouTube API audit. Google's current `videos.insert` page says unverified projects are no longer
  restricted to private, but if your first video turns up private anyway (the run log then says
  "WARNING video is 'private'"), apply for the audit here:
  <https://support.google.com/youtube/contact/yt_api_form> (YouTube API Services - Audit and Quota Extension form).
- Custom cover pictures only work once the channel is phone-verified (<https://www.youtube.com/verify>). If not,
  the video still goes up with an automatic thumbnail.

## 2. TikTok

1. Go to <https://developers.tiktok.com/>, log in, **Manage apps -> Connect an app** (individual developer is fine).
   Fill in name, icon, category, and the website `https://evenout.app` plus links to your terms and privacy page
   (`https://evenout.app/legal.html`).
2. **Add products**: **Login Kit** and **Content Posting API**. In Content Posting API switch **Direct Post** on.
3. **Scopes**: `user.info.basic` and `video.publish`.
4. **Login Kit -> Redirect URI**, platform **Desktop**: add exactly `http://localhost:8765/callback/`
   (with the slash at the end).
5. Submit the app for review (TikTok must approve the app before anyone can sign in to it; this can take a few days).
   Copy the **Client key** and **Client secret** from the app page.
6. Open three GitHub "New repository secret" pages (`TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`,
   `TIKTOK_REFRESH_TOKEN`), then on your computer:

   ```powershell
   py video/setup_tiktok.py
   ```

   Paste the Client key and secret when asked (hidden), sign in with the evenout TikTok account and allow. The script
   then puts each value on the clipboard in turn; paste it into its GitHub page and type `done`. Nothing is printed.

**What gets posted:** Last night (daily), Hot or lucky (Tue/Thu/Sat) and the long slate video, each marked as
**Your brand** (`brand_organic_toggle`, TikTok shows "Promotional content"). Board and Report never go to TikTok
(picks/odds count as gambling content there). Until the app is audited, the run skips TikTok quietly
("TikTok has not approved the app yet") instead of failing every day.

**Limits to know**
- **Private until audited.** Until TikTok audits the app, every post is private (`SELF_ONLY`, only you can see it),
  and the run log says so. Apply for the audit at <https://developers.tiktok.com/application/content-posting-api>.
  TikTok may also answer `unaudited_client_can_only_post_to_private_accounts`: then the TikTok account itself has to
  be set to private until the audit passes.
- **The TikTok key changes after every upload.** TikTok gives out a new refresh token every time it is used and the old
  one stops working. The workflow saves the new one into the `TIKTOK_REFRESH_TOKEN` secret by itself, as long as it has
  a GitHub key that may write secrets. Create it once:
  GitHub → your profile picture → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** →
  Generate new token → Repository access: *Only select repositories* → `markoras4/nhl-props` → Permissions:
  **Secrets: Read and write** → Generate. Copy it and add it in the repository as the secret **`GH_SECRETS_PAT`**.
  Without it the run shows "GH_SECRETS_PAT is not set" and the next TikTok upload fails until you run
  `py video/auth_tiktok.py` again. The refresh token also expires after 180 days if unused.
- TikTok allows about 6 upload starts per minute and a daily cap of posts per account; one or two a day is fine.

## 3. Try it

- Actions tab -> **Daily videos** -> **Run workflow**, tick **dry_run**: renders and shows what would be posted.
- On your computer: `py video/upload.py --manifest video/out/2026-11-16/manifest.json --dry-run`
- Post to one platform only: `--only youtube` or `--only tiktok`.
- To post a video again on purpose, delete its line from `data/video_uploads.json`.

Never paste the keys into a file in the repository, a chat, or an issue. If one leaks, delete the OAuth client
(Google) or reset the client secret (TikTok) and repeat the steps above.
