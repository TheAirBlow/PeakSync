import json
import os
import time
import hashlib
import datetime
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

PLAYLIST_A_ID = os.environ["PLAYLIST_A_ID"]
PLAYLIST_B_ID = os.environ["PLAYLIST_B_ID"]
YTM_COOKIE = os.environ.get("YTM_COOKIE", "")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
MIN_RETENTION_HOURS = float(os.environ.get("MIN_RETENTION_HOURS", "24"))
STATE_FILE = os.environ.get("STATE_FILE", "/data/state.json")
TOKEN_FILE = os.environ.get("TOKEN_FILE", "/data/token.json")

SCOPES = ["https://www.googleapis.com/auth/youtube"]

session = requests.Session()
BASE_HEADERS = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "accept": "*/*",
    "accept-encoding": "gzip, deflate",
    "content-type": "application/json",
    "origin": "https://music.youtube.com",
    "x-origin": "https://music.youtube.com",
    "x-goog-authuser": "0"
}

if YTM_COOKIE:
    BASE_HEADERS["cookie"] = YTM_COOKIE

CONTEXT = {
    "client": {
        "clientName": "WEB_REMIX",
        "clientVersion": "1.20260707.12.00",
        "hl": "en",
        "gl": "US",
    },
    "user": {},
}

def _sapisid_hash():
    sapisid = next(p.split("=", 1)[1] for p in YTM_COOKIE.split("; ") if p.startswith("SAPISID="))
    timestamp = str(int(time.time()))
    digest = hashlib.sha1(f"{timestamp} {sapisid} https://music.youtube.com".encode()).hexdigest()
    return f"SAPISIDHASH {timestamp}_{digest}"

def _ytm_post(endpoint, body, params=None):
    headers = dict(BASE_HEADERS)
    if YTM_COOKIE:
        headers["authorization"] = _sapisid_hash()
    response = session.post(
        f"https://music.youtube.com/youtubei/v1/{endpoint}",
        params={"alt": "json", **(params or {})},
        json=body,
        headers=headers,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]!r}")
    return response.json()

def fetch_playlist_video_ids(playlist_id):
    browse_id = playlist_id if playlist_id.startswith("VL") else f"VL{playlist_id}"
    data = _ytm_post("browse", {"context": CONTEXT, "browseId": browse_id})

    try:
        shelf = data["contents"]["twoColumnBrowseResultsRenderer"]["secondaryContents"][
            "sectionListRenderer"
        ]["contents"][0]["musicPlaylistShelfRenderer"]
    except KeyError as e:
        raise RuntimeError(f"HTTP 200 but unexpected body shape ({e}): {str(data)[:500]!r}") from e

    video_ids = []
    contents = shelf["contents"]
    continuation = shelf.get("continuations")

    while True:
        for entry in contents:
            item = entry.get("musicResponsiveListItemRenderer")
            if item is None:
                continue
            video_id = item.get("playlistItemData", {}).get("videoId")
            if video_id:
                video_ids.append(video_id)

        if not continuation:
            break

        ctoken = continuation[0]["nextContinuationData"]["continuation"]
        data = _ytm_post(
            "browse",
            {"context": CONTEXT},
            params={"ctoken": ctoken, "continuation": ctoken, "type": "next"},
        )
        continuation_contents = data.get("continuationContents", {}).get("musicPlaylistShelfContinuation", {})
        contents = continuation_contents.get("contents", [])
        continuation = continuation_contents.get("continuations")

    return video_ids

def load_credentials():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds

def _execute(request):
    return request.execute(num_retries=5)

def _list_playlist_b_items_once(youtube):
    items = {}
    page_token = None
    while True:
        response = _execute(youtube.playlistItems().list(
            part="contentDetails",
            playlistId=PLAYLIST_B_ID,
            maxResults=50,
            pageToken=page_token,
        ))
        for item in response["items"]:
            items[item["contentDetails"]["videoId"]] = item["id"]
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return items

def list_playlist_b_items(youtube):
    # page tokens occasionally 404 mid-listing, so restart from the first page
    for attempt in range(3):
        try:
            return _list_playlist_b_items_once(youtube)
        except HttpError as e:
            if e.resp.status != 404 or attempt == 2:
                raise
            time.sleep(2)

def add_to_playlist_b(youtube, video_id):
    response = _execute(youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": PLAYLIST_B_ID,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ))
    return response["id"]

def remove_from_playlist_b(youtube, playlist_item_id):
    _execute(youtube.playlistItems().delete(id=playlist_item_id))

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def sync_once(youtube, state):
    now = datetime.datetime.now(datetime.timezone.utc)
    a_ids = set(fetch_playlist_video_ids(PLAYLIST_A_ID))
    b_items = list_playlist_b_items(youtube)
    print(f"cycle: A={len(a_ids)} B={len(b_items)} missing_from_B={len(a_ids - set(b_items))} tracked={len(state)}", flush=True)

    for video_id in a_ids:
        if video_id not in b_items and video_id not in state:
            playlist_item_id = add_to_playlist_b(youtube, video_id)
            state[video_id] = {"added_at": now.isoformat(), "playlist_item_id": playlist_item_id}
            save_state(state)
            print(f"added {video_id} to B", flush=True)

    for video_id in list(state):
        if video_id in a_ids:
            continue
        entry = state[video_id]
        added_at = datetime.datetime.fromisoformat(entry["added_at"])
        age_hours = (now - added_at).total_seconds() / 3600
        playlist_item_id = b_items.get(video_id, entry.get("playlist_item_id"))
        if age_hours < MIN_RETENTION_HOURS and playlist_item_id:
            remove_from_playlist_b(youtube, playlist_item_id)
            print(f"removed {video_id} from B, only lasted {age_hours:.1f}h in A", flush=True)
        del state[video_id]
        save_state(state)

    for video_id in list(state):
        entry = state[video_id]
        added_at = datetime.datetime.fromisoformat(entry["added_at"])
        age_hours = (now - added_at).total_seconds() / 3600
        if age_hours >= MIN_RETENTION_HOURS:
            del state[video_id]

    save_state(state)

def make_client():
    return build("youtube", "v3", credentials=load_credentials())

youtube = make_client()
state = load_state()
print(f"started, polling every {POLL_INTERVAL_SECONDS}s", flush=True)

while True:
    try:
        sync_once(youtube, state)
    except Exception as e:
        print(f"sync failed: {e}", flush=True)
        # stale connections (e.g. broken pipe) are fixed by a fresh client
        try:
            youtube = make_client()
        except Exception as e:
            print(f"client rebuild failed: {e}", flush=True)
    time.sleep(POLL_INTERVAL_SECONDS)
