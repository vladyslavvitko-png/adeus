from __future__ import annotations

import base64
import email.utils
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from html import unescape
from typing import Any, Optional

from tzlocal import get_localzone

from .auth import gmail_service

LABEL_PRESETS = [
    {"id": "INBOX", "name": "Inbox"},
    {"id": "UNREAD", "name": "Unread"},
    {"id": "STARRED", "name": "Starred"},
    {"id": "IMPORTANT", "name": "Important"},
    {"id": "SENT", "name": "Sent"},
    {"id": "DRAFT", "name": "Drafts"},
]
METADATA_HEADERS = [
    "From",
    "To",
    "Cc",
    "Subject",
    "Date",
    "Message-ID",
    "In-Reply-To",
    "References",
    "Reply-To",
    "List-Unsubscribe",
    "List-Unsubscribe-Post",
    "List-Id",
    "Precedence",
    "Auto-Submitted",
]


def _header_map(headers: list[dict[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in headers:
        name = (item.get("name") or "").lower()
        if name and name not in out:
            out[name] = item.get("value") or ""
    return out


def _decode_mime(value: str) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _parse_from(raw: str) -> dict[str, str]:
    name, addr = email.utils.parseaddr(raw)
    display = _decode_mime(name) or addr or raw or "Unknown"
    return {"name": display, "email": addr, "raw": raw}


def _parse_date(raw: str, internal_ms: Optional[str] = None) -> dict[str, Any]:
    dt = None
    if raw:
        try:
            dt = email.utils.parsedate_to_datetime(raw)
        except Exception:
            dt = None
    if dt is None and internal_ms:
        try:
            dt = datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc)
        except Exception:
            dt = None
    if dt is None:
        return {"iso": None, "label": "", "raw": raw}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    now = datetime.now().astimezone()
    if local.date() == now.date():
        label = local.strftime("%-I:%M %p")
    elif local.year == now.year:
        label = local.strftime("%b %-d")
    else:
        label = local.strftime("%b %-d, %Y")
    return {"iso": dt.isoformat(), "label": label, "raw": raw}


def _b64(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


def _part_files(payload: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def walk(part: dict[str, Any]) -> None:
        if not part:
            return
        filename = part.get("filename") or ""
        mime = part.get("mimeType") or ""
        if filename:
            found.append({"filename": filename, "mime": mime})
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    return found


def _walk_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    mime = payload.get("mimeType") or "application/octet-stream"
    filename = payload.get("filename") or ""
    body = payload.get("body") or {}
    data = body.get("data")
    if data:
        found.append(
            {
                "mime": mime,
                "filename": filename,
                "bytes": _b64(data),
                "size": body.get("size") or 0,
                "attachment_id": body.get("attachmentId"),
            }
        )
    elif filename and body.get("attachmentId"):
        found.append(
            {
                "mime": mime,
                "filename": filename,
                "bytes": b"",
                "size": body.get("size") or 0,
                "attachment_id": body.get("attachmentId"),
            }
        )
    for part in payload.get("parts") or []:
        found.extend(_walk_parts(part))
    return found


def _strip_html(html: str) -> str:
    text = html or ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)</div>", "\n", text)
    text = re.sub(r"(?i)</tr>", "\n", text)
    text = re.sub(r"(?i)</h[1-6]>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _summarize(message: dict[str, Any], include_body: bool = False) -> dict[str, Any]:
    payload = message.get("payload") or {}
    headers = _header_map(payload.get("headers") or [])
    labels = message.get("labelIds") or []
    sender = _parse_from(headers.get("from", ""))
    date = _parse_date(headers.get("date", ""), message.get("internalDate"))
    item: dict[str, Any] = {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "snippet": unescape(message.get("snippet") or ""),
        "subject": _decode_mime(headers.get("subject", "")) or "(no subject)",
        "from": sender,
        "to": _decode_mime(headers.get("to", "")),
        "cc": _decode_mime(headers.get("cc", "")),
        "date": date,
        "unread": "UNREAD" in labels,
        "starred": "STARRED" in labels,
        "important": "IMPORTANT" in labels,
        "draft": "DRAFT" in labels,
        "labels": labels,
        "rfc_message_id": headers.get("message-id", ""),
        "in_reply_to": headers.get("in-reply-to", ""),
        "references": headers.get("references", ""),
        "reply_to": _decode_mime(headers.get("reply-to", "")),
    }
    files = _part_files(payload)
    item["attachments"] = [{"filename": part["filename"], "mime": part["mime"]} for part in files]
    item["has_pdf"] = any(
        part["filename"].lower().endswith(".pdf") or "pdf" in (part["mime"] or "").lower()
        for part in files
    )
    item["list_unsubscribe"] = bool(
        headers.get("list-unsubscribe") or headers.get("list-unsubscribe-post")
    )
    item["list_id"] = _decode_mime(headers.get("list-id", ""))
    item["bulk"] = (headers.get("precedence") or "").lower() in {"bulk", "list", "junk"}
    item["auto_submitted"] = bool(
        headers.get("auto-submitted")
        and headers.get("auto-submitted").strip().lower() not in {"", "no"}
    )
    if not include_body:
        return item

    parts = _walk_parts(payload)
    plains: list[str] = []
    htmls: list[str] = []
    attachments = []
    for part in parts:
        mime = (part["mime"] or "").lower()
        filename = part["filename"]
        if filename and not mime.startswith("text/"):
            attachments.append(
                {
                    "filename": filename,
                    "mime": part["mime"],
                    "size": part["size"],
                }
            )
            continue
        if mime == "text/plain" and part["bytes"] and not filename:
            plains.append(_decode_text(part["bytes"]))
        elif mime == "text/html" and part["bytes"] and not filename:
            htmls.append(_decode_text(part["bytes"]))
        elif filename:
            attachments.append(
                {
                    "filename": filename,
                    "mime": part["mime"],
                    "size": part["size"],
                }
            )

    if not plains and not htmls and payload.get("body", {}).get("data"):
        raw = _decode_text(_b64(payload["body"]["data"]))
        if (payload.get("mimeType") or "").startswith("text/html"):
            htmls.append(raw)
        else:
            plains.append(raw)

    text_plain = "\n\n".join(chunk.strip() for chunk in plains if chunk.strip())
    text_html = htmls[0] if htmls else ""
    item["body"] = {
        "text": text_plain or (_strip_html(text_html) if text_html else ""),
    }
    item["attachments"] = attachments
    return item


def list_messages(
    label: str = "INBOX",
    query: str = "",
    page_token: Optional[str] = None,
    max_results: int = 25,
) -> dict[str, Any]:
    service = gmail_service()
    kwargs: dict[str, Any] = {
        "userId": "me",
        "maxResults": max_results,
    }
    if query:
        kwargs["q"] = query
    if label and label != "ALL":
        kwargs["labelIds"] = [label]
    if page_token:
        kwargs["pageToken"] = page_token

    listing = service.users().messages().list(**kwargs).execute()
    ids = [m["id"] for m in listing.get("messages") or []]
    collected: dict[str, dict[str, Any]] = {}

    def on_message(request_id: str, response: dict[str, Any], exception: Optional[Exception]) -> None:
        if exception is None:
            collected[request_id] = _summarize(response)

    if ids:
        batch = service.new_batch_http_request(callback=on_message)
        for index, message_id in enumerate(ids):
            batch.add(
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=METADATA_HEADERS,
                ),
                request_id=str(index),
            )
        batch.execute()
    messages = [collected[str(i)] for i in range(len(ids)) if str(i) in collected]
    return {
        "messages": messages,
        "next_page_token": listing.get("nextPageToken"),
        "result_size_estimate": listing.get("resultSizeEstimate", len(messages)),
    }


def list_recent_messages(
    label: str = "INBOX",
    query: str = "",
    limit: int = 1000,
) -> dict[str, Any]:
    service = gmail_service()
    limit = max(1, min(int(limit or 1000), 1000))
    ids: list[str] = []
    page_token: Optional[str] = None
    while len(ids) < limit:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "maxResults": min(500, limit - len(ids)),
        }
        if query:
            kwargs["q"] = query
        if label and label != "ALL":
            kwargs["labelIds"] = [label]
        if page_token:
            kwargs["pageToken"] = page_token
        listing = service.users().messages().list(**kwargs).execute()
        ids.extend(m["id"] for m in listing.get("messages") or [])
        page_token = listing.get("nextPageToken")
        if not page_token:
            break
    ids = ids[:limit]
    collected: dict[str, dict[str, Any]] = {}

    def on_message(request_id: str, response: dict[str, Any], exception: Optional[Exception]) -> None:
        if exception is None:
            collected[request_id] = _summarize(response)

    for start in range(0, len(ids), 50):
        chunk = ids[start : start + 50]
        batch = service.new_batch_http_request(callback=on_message)
        for offset, message_id in enumerate(chunk):
            batch.add(
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=METADATA_HEADERS,
                ),
                request_id=str(start + offset),
            )
        batch.execute()
    messages = [collected[str(i)] for i in range(len(ids)) if str(i) in collected]
    return {"messages": messages, "limit": limit, "count": len(messages)}


BULK_ACTION_CAP = 2000


def list_matching_message_ids(query: str, limit: int = BULK_ACTION_CAP) -> dict[str, Any]:
    """Resolve a Gmail search query to message ids, capped for bulk actions."""
    if not (query or "").strip():
        raise ValueError("A Gmail search query is required.")
    service = gmail_service()
    limit = max(1, min(int(limit or BULK_ACTION_CAP), BULK_ACTION_CAP))
    ids: list[str] = []
    page_token: Optional[str] = None
    while len(ids) <= limit:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "q": query,
            "maxResults": min(500, limit + 1 - len(ids)),
            "includeSpamTrash": False,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        data = service.users().messages().list(**kwargs).execute()
        ids.extend(m["id"] for m in data.get("messages") or [])
        page_token = data.get("nextPageToken")
        if not page_token or len(ids) > limit:
            break
    capped = len(ids) > limit
    return {"ids": ids[:limit], "count": min(len(ids), limit), "capped": capped}


def archive_messages(message_ids: list[str]) -> dict[str, Any]:
    """Remove the INBOX label from messages (Gmail archive)."""
    ids = [str(item) for item in message_ids if item]
    if not ids:
        return {"count": 0}
    service = gmail_service()
    for start in range(0, len(ids), 1000):
        service.users().messages().batchModify(
            userId="me",
            body={"ids": ids[start : start + 1000], "removeLabelIds": ["INBOX"]},
        ).execute()
    return {"count": len(ids)}


def trash_messages(message_ids: list[str]) -> dict[str, Any]:
    """Move messages to Gmail Trash (recoverable for ~30 days).

    The TRASH label cannot be applied via batchModify, so this batches
    individual messages.trash calls instead.
    """
    ids = [str(item) for item in message_ids if item]
    if not ids:
        return {"count": 0, "failed": 0}
    service = gmail_service()
    failed: list[str] = []

    def on_result(request_id: str, _response: Any, exception: Optional[Exception]) -> None:
        if exception is not None:
            failed.append(request_id)

    for start in range(0, len(ids), 100):
        batch = service.new_batch_http_request(callback=on_result)
        for message_id in ids[start : start + 100]:
            batch.add(
                service.users().messages().trash(userId="me", id=message_id),
                request_id=message_id,
            )
        batch.execute()
    return {"count": len(ids) - len(failed), "failed": len(failed)}


def get_message(message_id: str) -> dict[str, Any]:
    service = gmail_service()
    raw = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    item = _summarize(raw, include_body=True)
    thread_id = item.get("thread_id")
    if not thread_id:
        item["thread"] = [item]
        return item
    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    item["thread"] = [_summarize(message, include_body=True) for message in thread.get("messages") or []]
    return item


def _list_thread_ids(query: str) -> list[str]:
    service = gmail_service()
    ids: list[str] = []
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "q": query,
            "maxResults": 500,
            "includeSpamTrash": False,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        data = service.users().threads().list(**kwargs).execute()
        ids.extend(
            str(item.get("id") or "")
            for item in (data.get("threads") or [])
            if item.get("id")
        )
        page_token = data.get("nextPageToken")
        if not page_token:
            return ids


def _list_thread_refs(query: str) -> list[dict[str, Any]]:
    service = gmail_service()
    refs: dict[str, dict[str, Any]] = {}
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {
            "userId": "me",
            "q": query,
            "maxResults": 500,
            "includeSpamTrash": False,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        data = service.users().messages().list(**kwargs).execute()
        for message in data.get("messages") or []:
            thread_id = str(message.get("threadId") or "")
            message_id = str(message.get("id") or "")
            if not thread_id or not message_id:
                continue
            ref = refs.setdefault(
                thread_id,
                {"thread_id": thread_id, "message_ids": []},
            )
            ref["message_ids"].append(message_id)
        page_token = data.get("nextPageToken")
        if not page_token:
            return list(refs.values())


def list_thread_ids_since(start_date: str) -> list[str]:
    try:
        local_midnight = datetime.strptime(start_date, "%Y-%m-%d").replace(
            tzinfo=get_localzone()
        )
    except ValueError as exc:
        raise ValueError("Start date must use YYYY-MM-DD.") from exc
    return _list_thread_ids(f"after:{int(local_midnight.timestamp())}")


def list_thread_refs_since(start_date: str) -> list[dict[str, Any]]:
    try:
        local_midnight = datetime.strptime(start_date, "%Y-%m-%d").replace(
            tzinfo=get_localzone()
        )
    except ValueError as exc:
        raise ValueError("Start date must use YYYY-MM-DD.") from exc
    return _list_thread_refs(f"after:{int(local_midnight.timestamp())}")


def list_thread_ids_lookback(hours: int) -> list[str]:
    try:
        normalized = max(1, min(int(hours), 24 * 365))
    except (TypeError, ValueError) as exc:
        raise ValueError("Lookback must be between 1 and 8,760 hours.") from exc
    cutoff = datetime.now(timezone.utc) - timedelta(hours=normalized)
    return _list_thread_ids(f"after:{int(cutoff.timestamp())}")


def list_thread_refs_lookback(hours: int) -> list[dict[str, Any]]:
    try:
        normalized = max(1, min(int(hours), 24 * 365))
    except (TypeError, ValueError) as exc:
        raise ValueError("Lookback must be between 1 and 8,760 hours.") from exc
    cutoff = datetime.now(timezone.utc) - timedelta(hours=normalized)
    return _list_thread_refs(f"after:{int(cutoff.timestamp())}")


def get_thread(thread_id: str) -> dict[str, Any]:
    if not thread_id:
        raise ValueError("Missing thread id.")
    service = gmail_service()
    raw = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="full")
        .execute()
    )
    messages = [
        _summarize(message, include_body=True)
        for message in (raw.get("messages") or [])
    ]
    non_drafts = [message for message in messages if not message.get("draft")]
    thread = non_drafts or messages
    if not thread:
        raise ValueError("Gmail returned an empty thread.")
    item = dict(thread[-1])
    item["thread_id"] = str(raw.get("id") or thread_id)
    item["thread"] = thread
    return item


def thread_plain_text(message: dict[str, Any], limit: int = 12000) -> str:
    chunks = []
    for item in message.get("thread") or [message]:
        sender = item.get("from") or {}
        body = ((item.get("body") or {}).get("text") or item.get("snippet") or "").strip()
        chunks.append(
            f"Subject: {item.get('subject')}\n"
            f"From: {sender.get('name')} <{sender.get('email')}>\n"
            f"To: {item.get('to') or ''}\n"
            f"Date: {(item.get('date') or {}).get('label')}\n\n"
            f"{body}"
        )
    return "\n\n----------\n\n".join(chunks).strip()[:limit]


def thread_has_draft(thread_id: str) -> bool:
    if not thread_id:
        return False
    service = gmail_service()
    thread = (
        service.users()
        .threads()
        .get(userId="me", id=thread_id, format="minimal")
        .execute()
    )
    for message in thread.get("messages") or []:
        if "DRAFT" in (message.get("labelIds") or []):
            return True
    return False


def find_thread_draft(thread_id: str) -> Optional[str]:
    if not thread_id:
        return None
    service = gmail_service()
    page_token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {"userId": "me", "maxResults": 100}
        if page_token:
            kwargs["pageToken"] = page_token
        data = service.users().drafts().list(**kwargs).execute()
        for draft in data.get("drafts") or []:
            message = draft.get("message") or {}
            if str(message.get("threadId") or "") == str(thread_id):
                return str(draft.get("id") or "") or None
        page_token = data.get("nextPageToken")
        if not page_token:
            return None


def get_reply_draft(draft_id: str) -> dict[str, Any]:
    if not draft_id:
        raise ValueError("Missing draft id.")
    service = gmail_service()
    draft = (
        service.users()
        .drafts()
        .get(userId="me", id=draft_id, format="full")
        .execute()
    )
    message = draft.get("message") or {}
    parsed = _summarize(message, include_body=True)
    return {
        "draft_id": str(draft.get("id") or draft_id),
        "message_id": str(message.get("id") or ""),
        "thread_id": str(message.get("threadId") or ""),
        "body": ((parsed.get("body") or {}).get("text") or "").strip(),
        "to": parsed.get("to") or "",
        "subject": parsed.get("subject") or "",
    }


def last_inbound(message: dict[str, Any], owner_email: str) -> dict[str, Any]:
    owner = (owner_email or "").strip().lower()
    thread = message.get("thread") or [message]
    inbound = None
    for item in thread:
        sender = ((item.get("from") or {}).get("email") or "").strip().lower()
        if sender and sender != owner:
            inbound = item
    return inbound or (thread[-1] if thread else message)


def _reply_raw(
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> str:
    from email.mime.text import MIMEText

    if not (to_addr or "").strip():
        raise ValueError("Missing recipient for draft.")
    text = (body or "").strip()
    if not text:
        raise ValueError("Draft body is empty.")
    subj = (subject or "").strip() or "(no subject)"
    if not re.match(r"(?i)^re\s*:", subj):
        subj = f"Re: {subj}"
    mime = MIMEText(text, "plain", "utf-8")
    mime["To"] = to_addr.strip()
    mime["Subject"] = subj
    reply_id = (in_reply_to or "").strip()
    refs = (references or "").strip()
    if reply_id:
        mime["In-Reply-To"] = reply_id
        mime["References"] = f"{refs} {reply_id}".strip() if refs else reply_id
    elif refs:
        mime["References"] = refs
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")


def create_reply_draft(
    thread_id: str,
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> dict[str, Any]:
    if not thread_id:
        raise ValueError("Missing thread id for draft.")
    raw = _reply_raw(to_addr, subject, body, in_reply_to, references)
    service = gmail_service()
    created = (
        service.users()
        .drafts()
        .create(
            userId="me",
            body={"message": {"raw": raw, "threadId": thread_id}},
        )
        .execute()
    )
    draft_message = created.get("message") or {}
    return {
        "draft_id": created.get("id"),
        "message_id": draft_message.get("id"),
        "thread_id": draft_message.get("threadId") or thread_id,
    }


def update_reply_draft(
    draft_id: str,
    thread_id: str,
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> dict[str, Any]:
    if not draft_id:
        raise ValueError("Missing draft id.")
    if not thread_id:
        raise ValueError("Missing thread id for draft.")
    raw = _reply_raw(to_addr, subject, body, in_reply_to, references)
    service = gmail_service()
    updated = (
        service.users()
        .drafts()
        .update(
            userId="me",
            id=draft_id,
            body={"message": {"raw": raw, "threadId": thread_id}},
        )
        .execute()
    )
    draft_message = updated.get("message") or {}
    return {
        "draft_id": updated.get("id") or draft_id,
        "message_id": draft_message.get("id"),
        "thread_id": draft_message.get("threadId") or thread_id,
    }


def list_labels() -> list[dict[str, str]]:
    service = gmail_service()
    data = service.users().labels().list(userId="me").execute()
    extras = []
    for label in data.get("labels") or []:
        if label.get("type") == "user":
            extras.append({"id": label["id"], "name": label["name"]})
    extras.sort(key=lambda item: item["name"].lower())
    return LABEL_PRESETS + extras
