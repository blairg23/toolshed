from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_TOKENS_DIR = Path(__file__).parent.parent / "tokens"


def _token_path(account: str) -> Path:
    return _TOKENS_DIR / f"{account}.json"


def _client_config() -> dict:
    load_dotenv(Path(__file__).parent.parent / ".env")
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in GmailTools/.env"
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }


def authenticate(account: str) -> Credentials:
    """Return valid credentials for account, running the auth flow if needed."""
    _TOKENS_DIR.mkdir(exist_ok=True)
    token_file = _token_path(account)
    creds: Credentials | None = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
        creds = flow.run_local_server(port=0)

    token_file.write_text(creds.to_json())
    return creds


def build_service(account: str):
    """Return an authenticated Gmail API service for account."""
    creds = authenticate(account)
    return build("gmail", "v1", credentials=creds)
