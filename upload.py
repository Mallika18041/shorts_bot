"""YouTube uploader. First run opens a browser for Google sign-in and saves token.json."""
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN = Path("token.json")
SECRETS = Path("client_secret.json")


def get_service():
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload(video_path, data, privacy="private"):
    yt = get_service()
    title = data["title"][:90]
    if "#Shorts" not in title:
        title = (title[:80] + " #Shorts").strip()
    body = {
        "snippet": {
            "title": title,
            "description": data["description"] + "\n\n#Shorts #Telugu #తెలుగు",
            "tags": data.get("tags", []),
            "categoryId": "27",  # Education
            "defaultLanguage": "te",
            "defaultAudioLanguage": "te",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # AI voice/visuals disclosure
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"Uploading {int(status.progress() * 100)}%")
    return resp["id"]
