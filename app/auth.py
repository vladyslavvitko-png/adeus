from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
TOKEN_PATH = ROOT / "token.json"
PENDING_PATH = ROOT / ".oauth_pending.json"
READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
SCOPES = [READONLY_SCOPE, COMPOSE_SCOPE, MODIFY_SCOPE]
DRAFT_SCOPES = {COMPOSE_SCOPE, MODIFY_SCOPE, "https://mail.google.com/"}
MODIFY_SCOPES = {MODIFY_SCOPE, "https://mail.google.com/"}
LOGIN_PORT = 8753


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def client_config() -> dict:
    client_id = _env("GOOGLE_CLIENT_ID")
    client_secret = _env("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise FileNotFoundError(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env."
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "project_id": _env("GOOGLE_PROJECT_ID"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }


def load_credentials() -> Optional[Credentials]:
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Use the scopes stored in the token itself so a token issued before a
    # scope was added to SCOPES keeps refreshing instead of erroring.
    data["client_id"] = _env("GOOGLE_CLIENT_ID")
    data["client_secret"] = _env("GOOGLE_CLIENT_SECRET")
    creds = Credentials.from_authorized_user_info(data)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_credentials(creds)
    if creds and creds.valid:
        return creds
    return None


def save_credentials(creds: Credentials) -> None:
    data = json.loads(creds.to_json())
    data.pop("client_id", None)
    data.pop("client_secret", None)
    TOKEN_PATH.write_text(json.dumps(data))
    try:
        TOKEN_PATH.chmod(0o600)
    except OSError:
        pass


def clear_credentials() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    if PENDING_PATH.exists():
        PENDING_PATH.unlink()


def granted_scopes(creds: Optional[Credentials] = None) -> list[str]:
    if TOKEN_PATH.exists():
        try:
            data = json.loads(TOKEN_PATH.read_text())
            file_scopes = data.get("scopes")
            if isinstance(file_scopes, list) and file_scopes:
                return [str(scope) for scope in file_scopes]
        except Exception:
            pass
    creds = creds or load_credentials()
    if not creds:
        return []
    return [str(scope) for scope in (creds.scopes or [])]


def can_create_drafts(creds: Optional[Credentials] = None) -> bool:
    return bool(DRAFT_SCOPES.intersection(granted_scopes(creds)))


def can_modify(creds: Optional[Credentials] = None) -> bool:
    return bool(MODIFY_SCOPES.intersection(granted_scopes(creds)))


_LOGIN_LOCK = threading.Lock()


def _assert_login_port_free() -> None:
    """Fail fast when the OAuth callback port is held by an abandoned attempt."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", LOGIN_PORT))
    except OSError as exc:
        raise RuntimeError(
            "A previous Gmail sign-in is still waiting on the callback port. "
            "Finish it in the browser, or wait up to two minutes and try again."
        ) from exc
    finally:
        probe.close()


def run_desktop_login(force: bool = False) -> Credentials:
    """Open the system browser and complete Google's installed-app loopback flow."""
    existing = load_credentials()
    if existing and not force and can_create_drafts(existing) and can_modify(existing):
        return existing
    if not _LOGIN_LOCK.acquire(blocking=False):
        raise RuntimeError(
            "A Gmail sign-in is already in progress. Finish it in the browser window."
        )
    try:
        _assert_login_port_free()
        flow = InstalledAppFlow.from_client_config(
            client_config(),
            scopes=SCOPES,
        )
        creds = flow.run_local_server(
            host="127.0.0.1",
            bind_addr="127.0.0.1",
            port=LOGIN_PORT,
            open_browser=True,
            prompt="consent",
            access_type="offline",
            authorization_prompt_message="",
            success_message=(
                "Adeus is connected. You can close this tab and return to the app."
            ),
            timeout_seconds=120,
        )
    finally:
        _LOGIN_LOCK.release()
    save_credentials(creds)
    if PENDING_PATH.exists():
        PENDING_PATH.unlink()
    return creds


def gmail_service(creds: Optional[Credentials] = None):
    creds = creds or load_credentials()
    if not creds:
        raise PermissionError("Not connected to Gmail.")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def profile() -> Optional[dict]:
    creds = load_credentials()
    if not creds:
        return None
    service = gmail_service(creds)
    data = service.users().getProfile(userId="me").execute()
    return {
        "email": data.get("emailAddress"),
        "messages_total": data.get("messagesTotal"),
        "threads_total": data.get("threadsTotal"),
    }


def client_info() -> dict:
    installed = client_config()["installed"]
    return {
        "project_id": installed.get("project_id") or None,
        "file": ".env",
    }
