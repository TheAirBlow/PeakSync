# PeakSync
Periodically copies tracks from a YouTube Music playlist A into another playlist B.

A track stays in B forever once it has been present for at least `MIN_RETENTION_HOURS` (default 24h), whether it's still in A or not. If it gets removed from A before reaching that age, it's deleted from B too.

## 1. Get a YouTube Data API OAuth client
1. In the [Google Cloud Console](https://console.cloud.google.com/), create/select a project.
2. Enable the **YouTube Data API v3**.
3. Under **APIs & Services > Credentials**, create an **OAuth client ID** of type
   **Desktop app**.
4. Download the JSON and save it as `data/client_secret.json`.

## 2. Generate a token
This needs a browser, so run it on your machine before deploying:

```bash
mkdir -p data
pip install -r requirements.txt
python auth_setup.py data/client_secret.json data/token.json
```

This opens a browser to authorize, then saves `data/token.json`.

## 3. Get your YT Music cookie
1. Log into [music.youtube.com](https://music.youtube.com) in a browser.
2. Open DevTools > Network, reload, click any request to `music.youtube.com`.
3. Copy the full value of the `cookie` request header.
4. Paste it into `.env` as `YTM_COOKIE` (see `.env.example`).

This cookie is what lets the script read playlist A without spending API quota. If your playlist is public, this is not required.

## 4. Configure
```bash
cp .env.example .env
```

Fill in `.env`:
- `PLAYLIST_A_ID` / `PLAYLIST_B_ID` - the `list=` value from each playlist's URL.
- `POLL_INTERVAL_SECONDS` - how often to check A (default 300s).
- `MIN_RETENTION_HOURS` - the grace period (default 24).
- `YTM_COOKIE` - from step 3.

## 5. Run
```bash
docker compose up -d --build
```

Logs:
```bash
docker compose logs -f
```

State (which tracks are still within their grace period) is kept in `data/state.json`,
persisted on the host via the bind mount, so restarts don't lose track of pending items.

## Notes
- The OAuth token refreshes itself indefinitely as long as the refresh token stays valid, so it usually lasts forever.
- The YTM cookie might eventually expire, or just not work if you move it across machines.
