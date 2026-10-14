from __future__ import annotations

import re
from typing import Any, Optional

import httpx

from . import settings

NOTION_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
_TITLE_PROPERTIES: dict[str, str] = {}


class NotionError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.notion_api_key() and settings.notion_database_id())


def _database_id(value: Optional[str] = None) -> str:
    raw = (value or settings.notion_database_id() or "").strip()
    matches = re.findall(
        r"(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-"
        r"[a-fA-F0-9]{4}-[a-fA-F0-9]{12}|[a-fA-F0-9]{32})",
        raw,
    )
    if not matches:
        raise NotionError("Enter a valid Notion database ID or database URL.")
    value = matches[-1].replace("-", "").lower()
    return (
        f"{value[:8]}-{value[8:12]}-{value[12:16]}-"
        f"{value[16:20]}-{value[20:]}"
    )


def _headers() -> dict[str, str]:
    token = settings.notion_api_key()
    if not token:
        raise NotionError("Set NOTION_API_KEY in .env first.")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(
    method: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.request(
                method,
                f"{NOTION_BASE}{path}",
                headers=_headers(),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise NotionError("Could not reach Notion.") from exc
    if response.status_code == 401:
        raise NotionError("Notion rejected the integration token.")
    if response.status_code == 403:
        raise NotionError(
            "Share the target Notion database with this integration, then try again."
        )
    if response.status_code == 404:
        raise NotionError(
            "Notion could not find the database. Check its ID and integration access."
        )
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str((response.json() or {}).get("message") or "")
        except Exception:
            pass
        raise NotionError(detail or f"Notion returned {response.status_code}.")
    data = response.json()
    return data if isinstance(data, dict) else {}


def _rich_text(content: str, href: Optional[str] = None) -> list[dict[str, Any]]:
    raw = content or ""
    chunks = [raw[index : index + 1800] for index in range(0, len(raw), 1800)] or [""]
    rich_text = []
    for chunk in chunks:
        text: dict[str, Any] = {"content": chunk}
        if href:
            text["link"] = {"url": href}
        rich_text.append({"type": "text", "text": text})
    return rich_text


def create_todo(
    title: str,
    details: str,
    subject: str,
    sender: str,
    category: str,
    thread_id: str,
) -> dict[str, str]:
    database_id = _database_id()
    title_property = _TITLE_PROPERTIES.get(database_id)
    if not title_property:
        database = _request("GET", f"/databases/{database_id}")
        properties = database.get("properties") or {}
        title_property = next(
            (
                name
                for name, prop in properties.items()
                if isinstance(prop, dict) and prop.get("type") == "title"
            ),
            None,
        )
    if not title_property:
        raise NotionError("The Notion database has no title property.")
    _TITLE_PROPERTIES[database_id] = title_property
    gmail_url = (
        f"https://mail.google.com/mail/u/0/#all/{thread_id}" if thread_id else ""
    )
    payload: dict[str, Any] = {
        "parent": {"database_id": database_id},
        "properties": {
            title_property: {
                "title": _rich_text((title or f"Email action: {subject}")[:200])
            }
        },
        "children": [
            {
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "rich_text": _rich_text(title or "Handle email action"),
                    "checked": False,
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": _rich_text(
                        f"Email: {subject}\nFrom: {sender}\nCategory: {category}"
                    )
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": _rich_text(details or "")},
            },
        ],
    }
    if gmail_url:
        payload["children"].append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": _rich_text("Open thread in Gmail", gmail_url)
                },
            }
        )
    page = _request("POST", "/pages", payload)
    return {
        "page_id": str(page.get("id") or ""),
        "url": str(page.get("url") or ""),
    }
