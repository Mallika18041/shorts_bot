"""Sign in to YouTube again on your PC and update the TOKEN_JSON secret. Run: python reauth.py"""
import subprocess
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REPO = "Mallika18041/shorts_bot"

secrets = Path("client_secret.json")
if not secrets.exists():
    sys.exit("client_secret.json is not in this folder. Put your downloaded Google client file here, named client_secret.json.")
creds = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES).run_local_server(port=0)
Path("token.json").write_text(creds.to_json())
subprocess.run(["gh", "secret", "set", "TOKEN_JSON", "--repo", REPO], input=creds.to_json().encode(), check=True)
print("Done. The TOKEN_JSON secret is updated. Now tap Approve again in Telegram, or send /approve N.")
