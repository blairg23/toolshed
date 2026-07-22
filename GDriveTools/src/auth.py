from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_TOKENS_DIR = Path(__file__).parent.parent / "tokens"
_TOKEN_FILE = _TOKENS_DIR / "drive.json"


def _client_config() -> dict:
    load_dotenv(Path(__file__).parent.parent / ".env")
    client_id = os.environ.get("GDRIVE_CLIENT_ID")
    client_secret = os.environ.get("GDRIVE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET must be set in GDriveTools/.env")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }


def authenticate() -> Credentials:
    """Return valid Drive credentials, running the auth flow if needed."""
    _TOKENS_DIR.mkdir(exist_ok=True)
    creds: Credentials | None = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
        creds = flow.run_local_server(port=0)

    _TOKEN_FILE.write_text(creds.to_json())
    _TOKEN_FILE.chmod(0o600)
    return creds


def build_service():
    """Return an authenticated Drive API v3 service."""
    creds = authenticate()
    return build("drive", "v3", credentials=creds)
