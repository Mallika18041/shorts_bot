# Telugu Facts Shorts Bot

Makes one vertical Telugu "surprising fact" Short (script -> voice -> stock video -> captions) and optionally uploads it to your channel as **private** so you can review and publish.

## One-time setup

1. Install Python 3.10+ and ffmpeg (`ffmpeg -version` must work).
2. Install the Telugu font **Noto Sans Telugu** (needed for burned-in captions).
3. `pip install -r requirements.txt`
4. Free API keys:
   - Gemini: https://aistudio.google.com/apikey
   - Pexels: https://www.pexels.com/api/
5. Set them:
   - Windows: `setx GEMINI_API_KEY "..."` and `setx PEXELS_API_KEY "..."` (reopen the terminal)
   - Mac/Linux: `export GEMINI_API_KEY=...` and `export PEXELS_API_KEY=...`
6. YouTube upload access:
   - Go to https://console.cloud.google.com, create a project, enable **YouTube Data API v3**.
   - Credentials -> Create OAuth client ID -> **Desktop app** -> download JSON, save here as `client_secret.json`.
   - OAuth consent screen: add your own Gmail as a test user.

## Run

```
python make_short.py              # just make the video (output/ folder)
python make_short.py --upload     # make + upload as private
```

First upload opens a browser to sign in; after that it is automatic.

## Daily automation

- Windows: Task Scheduler -> run `python make_short.py --upload` daily.
- Mac/Linux: `crontab -e` -> `0 18 * * * cd /path/to/shorts_bot && python make_short.py --upload`

## Important notes

- Unaudited API projects can only upload as private. Apply for the audit at https://support.google.com/youtube/contact/yt_api_form to publish directly. Until then, publish from YouTube Studio (takes seconds).
- Daily quota allows about 6 uploads.
- Watch the first videos yourself: AI facts can be wrong, and reviewing keeps the channel safe from "mass-produced content" demonetization.
- Pexels clips are free to use; keep the AI disclosure flag on.

## Fully automatic daily run (no PC needed)

Push this folder to a **private** GitHub repo. In Settings -> Secrets and variables -> Actions add:
`GEMINI_API_KEY`, `PEXELS_API_KEY`, `CLIENT_SECRET_JSON` (contents of client_secret.json), `TOKEN_JSON` (contents of token.json, created after you run `python make_short.py --upload` once on your PC).

The workflow in `.github/workflows/daily.yml` then makes and uploads one Short every day at 5 PM IST. You can also run it from the Actions tab.
