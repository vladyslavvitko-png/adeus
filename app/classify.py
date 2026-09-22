from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from . import deepseek, settings
from .auth import ROOT

STORE_PATH = ROOT / "classifications.json"
MAX_BATCH = 20
REPLY_FIELDS = (
    "needs_reply",
    "needs_reply_reason",
    "reply_draft_id",
    "task_needed",
    "task_details",
    "deadline",
    "importance",
    "additional_details",
    "notion_page_id",
    "notion_url",
    "todo_title",
)
ALIASES = {
    "invoice-receipts": "invoice",
    "invoice-and-receipts": "invoice",
    "invoices": "invoice",
    "invoices-receipts": "invoice",
    "bill": "invoice",
    "bills": "invoice",
    "billing": "invoice",
    "receipt": "invoice",
    "receipts": "invoice",
    "order": "invoice",
    "orders": "invoice",
    "order-confirmation": "invoice",
    "newsletter": "social",
    "newsletters": "social",
    "digest": "social",
    "digests": "social",
    "promotion": "social",
    "promotions": "social",
    "promo": "social",
    "marketing": "social",
    "mailing-list": "social",
    "mailing-lists": "social",
    "subscription": "social",
    "subscriptions": "social",
    "blog": "social",
    "blogs": "social",
    "spam": "social",
    "private-mail": "private",
    "private-mails": "private",
    "socials": "social",
    "work-mail": "work",
    "professional": "work",
    "flights": "travel",
    "hotel": "travel",
    "unknown": "other",
    "system": "other",
    "alert": "other",
}
UNSUB_RE = re.compile(
    r"\bunsubscribe\b|\bopt[ -]?out\b|manage (?:your )?preferences|"
    r"view (?:this )?(?:email )?in (?:your )?browser|mailing list",
    re.I,
)
STORAGE_RE = re.compile(
    r"\b(?:no memory|memory left|out of (?:disk|space|storage|memory)|"
    r"disk (?:full|space)|storage (?:full|quota|almost|low)|"
    r"quota (?:exceeded|almost|warning)|mailbox (?:full|almost)|"
    r"backup failed|low (?:disk|storage)|running out of (?:space|storage|memory)|"
    r"free up (?:space|storage)|cloud.{0,40}(?:full|memory|disk|quota))\b",
    re.I,
)
INVOICE_RE = re.compile(
    r"\b(?:invoice|tax invoice|receipt|order confirmation|payment (?:received|due|receipt)|"
    r"paid in full|billing statement|your order|purchase confirmation|"
    r"invoice[-_ ]?\d+|receipt[-_ ]?\d+)\b",
    re.I,
)


def category_map() -> dict[str, str]:
    return {item["slug"]: item["name"] for item in settings.classify_categories()}


def _fallback_slug(mapping: Optional[dict[str, str]] = None) -> str:
    names = mapping if mapping is not None else category_map()
    if "other" in names:
        return "other"
    if names:
        return next(reversed(list(names)))
    return "other"


def system_prompt() -> str:
    mapping = category_map()
    slugs = ", ".join(mapping) or "other"
    example = next(iter(mapping), "other")
    rules = []
    if "private" in mapping and "social" in mapping:
        rules.append(
            "If unsubscribe or mailing_list is true, never use private — that is a list, blog, or subscription, usually social."
        )
    if "other" in mapping:
        rules.append(
            "Cloud/disk/memory/quota/backup alerts are other, never invoice, even if they mention billing language loosely."
        )
    if "invoice" in mapping:
        rules.append(
            "pdf true supports invoice only when the subject, snippet, or filename is about an invoice, receipt, order, tax document, or paid ticket. A PDF alone is not an invoice."
        )
    extra = ""
    if rules:
        numbered = " ".join(f"{index}) {rule}" for index, rule in enumerate(rules, start=1))
        extra = f" Hard rules you must obey: {numbered}"
    return (
        "You classify Gmail for Adeus. Follow the user's sorting rules. "
        f"Allowed category slugs only: {slugs}. "
        "Each email includes unsubscribe, mailing_list, pdf, and files flags from Gmail."
        f"{extra} "
        'Reply with JSON only, shaped as '
        f'{{"items":[{{"id":"...","category":"{example}"}}]}}.'
    )


def prompt_hash(prompt: Optional[str] = None) -> str:
    text = (prompt if prompt is not None else settings.classify_prompt()).encode("utf-8")
    return hashlib.sha256(text).hexdigest()[:16]


def _preview(prompt: str) -> str:
    compact = " ".join((prompt or "").split())
    if len(compact) > 140:
        return compact[:137] + "..."
    return compact


def load_store() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {"prompt_hash": "", "items": {}}
    try:
        data = json.loads(STORE_PATH.read_text())
    except json.JSONDecodeError:
        return {"prompt_hash": "", "items": {}}
    if not isinstance(data, dict):
        return {"prompt_hash": "", "items": {}}
    items = data.get("items")
    if not isinstance(items, dict):
        items = {}
    tray = data.get("tray") if isinstance(data.get("tray"), list) else []
    return {
        "prompt_hash": data.get("prompt_hash") or "",
        "items": items,
        "tray": tray,
        "label": data.get("label") or "INBOX",
        "query": data.get("query") or "",
        "sorted_at": data.get("sorted_at") or "",
    }


def save_store(store: dict[str, Any]) -> None:
    STORE_PATH.write_text(json.dumps(store, indent=2) + "\n")
    try:
        STORE_PATH.chmod(0o600)
    except OSError:
        pass


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def normalize_category(value: str) -> dict[str, str]:
    mapping = category_map()
    fallback = _fallback_slug(mapping)
    slug = _slug(value)
    alias = ALIASES.get(slug, slug) or fallback
    if alias in mapping:
        return {"category": alias, "label": mapping[alias]}
    if slug in mapping:
        return {"category": slug, "label": mapping[slug]}
    wanted = slug or alias
    for key, name in mapping.items():
        if _slug(name) == wanted:
            return {"category": key, "label": name}
    return {"category": fallback, "label": mapping.get(fallback, fallback.title())}


def attach_categories(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    store = load_store()
    items = store.get("items") or {}
    decorated = []
    for message in messages:
        copy = dict(message)
        hit = items.get(str(copy.get("id") or ""))
        if hit:
            copy["category"] = hit.get("category")
            copy["category_label"] = hit.get("label")
            for key in REPLY_FIELDS:
                if key in hit:
                    copy[key] = hit.get(key)
        decorated.append(copy)
    return decorated


def cached_items() -> dict[str, Any]:
    store = load_store()
    current = prompt_hash()
    items = store.get("items") or {}
    tray = store.get("tray") if isinstance(store.get("tray"), list) else []
    return {
        "prompt_hash": store.get("prompt_hash") or "",
        "current_prompt_hash": current,
        "stale": bool(store.get("prompt_hash") and store.get("prompt_hash") != current),
        "items": items,
        "tray": attach_categories(tray),
        "label": store.get("label") or "INBOX",
        "query": store.get("query") or "",
        "sorted_at": store.get("sorted_at") or "",
        "count": len(items),
    }


def _tray_item(message: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "id",
        "thread_id",
        "snippet",
        "subject",
        "from",
        "to",
        "date",
        "unread",
        "starred",
        "important",
        "attachments",
        "has_pdf",
        "list_unsubscribe",
        "list_id",
        "bulk",
        "auto_submitted",
        "category",
        "category_label",
        "needs_reply",
        "needs_reply_reason",
        "reply_draft_id",
        "task_needed",
        "task_details",
        "deadline",
        "importance",
        "additional_details",
        "notion_page_id",
        "notion_url",
        "todo_title",
    )
    return {key: message.get(key) for key in keep if key in message or key in {"id", "subject", "snippet", "from"}}


def save_tray(messages: list[dict[str, Any]], label: str = "INBOX", query: str = "") -> dict[str, Any]:
    store = load_store()
    items = store.get("items") or {}
    tray = []
    for message in messages:
        message_id = str(message.get("id") or "")
        if not message_id:
            continue
        copy = _tray_item(message)
        copy["id"] = message_id
        hit = items.get(message_id)
        if hit:
            copy["category"] = hit.get("category")
            copy["category_label"] = hit.get("label")
            for key in REPLY_FIELDS:
                if key in hit:
                    copy[key] = hit.get(key)
        tray.append(copy)
    store["tray"] = tray
    store["label"] = label or "INBOX"
    store["query"] = query or ""
    store["sorted_at"] = datetime.now(timezone.utc).isoformat()
    save_store(store)
    return cached_items()


def merge_tray(messages: list[dict[str, Any]], label: str = "ALL", query: str = "") -> dict[str, Any]:
    store = load_store()
    existing = store.get("tray") if isinstance(store.get("tray"), list) else []
    incoming = [_tray_item(message) for message in messages if message.get("id")]
    incoming_threads = {
        str(message.get("thread_id") or message.get("id") or "")
        for message in incoming
    }
    retained = [
        message
        for message in existing
        if str(message.get("thread_id") or message.get("id") or "") not in incoming_threads
    ]
    store["tray"] = incoming + retained
    store["label"] = label or "ALL"
    store["query"] = query or ""
    store["sorted_at"] = datetime.now(timezone.utc).isoformat()
    save_store(store)
    return cached_items()


def analyzed_message_ids() -> set[str]:
    items = load_store().get("items") or {}
    return {
        str(message_id)
        for message_id, item in items.items()
        if isinstance(item, dict) and item.get("category")
    }


def _parse_items(text: str) -> list[Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            start = raw.find("[")
            end = raw.rfind("]")
        if start == -1 or end <= start:
            raise deepseek.DeepSeekError("DeepSeek did not return JSON for classification.")
        data = json.loads(raw[start : end + 1])
    if isinstance(data, dict):
        items = data.get("items")
        if items is None:
            items = data.get("categories") or data.get("results") or data.get("emails")
        if isinstance(items, dict):
            return [{"id": key, "category": value} for key, value in items.items()]
        return items if isinstance(items, list) else []
    if isinstance(data, list):
        return data
    return []


def _file_names(message: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in message.get("attachments") or []:
        if isinstance(item, dict) and item.get("filename"):
            names.append(str(item["filename"])[:80])
        elif isinstance(item, str) and item.strip():
            names.append(item.strip()[:80])
    return names[:8]


def _compact(message: dict[str, Any]) -> dict[str, Any]:
    sender = message.get("from") or {}
    if isinstance(sender, str):
        sender = {"name": sender, "email": ""}
    snippet = (message.get("snippet") or "").replace("\n", " ").strip()
    names = _file_names(message)
    blob = " ".join(
        [
            message.get("subject") or "",
            snippet,
            " ".join(names),
        ]
    )
    return {
        "id": str(message.get("id") or ""),
        "from": f"{sender.get('name') or ''} <{sender.get('email') or ''}>".strip(),
        "subject": (message.get("subject") or "")[:180],
        "snippet": snippet[:280],
        "unsubscribe": bool(
            message.get("list_unsubscribe") or UNSUB_RE.search(blob)
        ),
        "mailing_list": bool(message.get("list_id") or message.get("bulk")),
        "pdf": bool(message.get("has_pdf")),
        "system": bool(message.get("auto_submitted")),
        "files": names,
    }


def apply_guardrails(message: dict[str, Any], category: str) -> str:
    compact = _compact(message)
    blob = " ".join(
        [
            compact.get("subject") or "",
            compact.get("snippet") or "",
            " ".join(compact.get("files") or []),
        ]
    )
    mapping = category_map()
    slug = category if category in mapping else normalize_category(category)["category"]
    listed = bool(compact.get("unsubscribe") or compact.get("mailing_list"))
    if slug == "private" and listed and "social" in mapping:
        return "social"
    if STORAGE_RE.search(blob) and "other" in mapping:
        return "other"
    if slug == "invoice" and "invoice" in mapping and not INVOICE_RE.search(blob):
        if compact.get("system") or re.search(
            r"\b(?:alert|warning|monitoring|threshold|cpu|quota)\b", blob, re.I
        ):
            return "other" if "other" in mapping else slug
    if slug != "invoice" and "invoice" in mapping and compact.get("pdf") and INVOICE_RE.search(blob):
        return "invoice"
    return slug if slug in mapping else _fallback_slug(mapping)


def classify_messages(
    messages: list[dict[str, Any]],
    force: bool = False,
    prompt: Optional[str] = None,
) -> dict[str, Any]:
    if len(messages) > MAX_BATCH:
        raise ValueError(f"Send at most {MAX_BATCH} letters at a time.")
    if prompt is not None and prompt.strip():
        used = prompt.strip()
    else:
        used = settings.classify_prompt()
    current_hash = prompt_hash(used)
    store = load_store()
    if store.get("prompt_hash") != current_hash:
        store = {"prompt_hash": current_hash, "items": {}}
    items: dict[str, Any] = store.setdefault("items", {})
    pending = []
    for message in messages:
        message_id = str(message.get("id") or "")
        if not message_id:
            continue
        if not force and message_id in items:
            continue
        pending.append(message)

    if pending:
        payload = [_compact(item) for item in pending]
        user = f"{used.strip()}\n\nEmails:\n{json.dumps(payload, ensure_ascii=False)}"
        text = deepseek.chat(
            system_prompt(),
            user,
            temperature=0.1,
            timeout=120.0,
            json_mode=True,
        )
        parsed = _parse_items(text)
        found: dict[str, dict[str, str]] = {}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            message_id = str(entry.get("id") or entry.get("message_id") or "")
            category = entry.get("category") or entry.get("label") or "other"
            if message_id:
                found[message_id] = normalize_category(str(category))
        for message in pending:
            message_id = str(message.get("id") or "")
            guessed = found.get(message_id) or normalize_category("other")
            slug = apply_guardrails(message, guessed["category"])
            entry = normalize_category(slug)
            previous = items.get(message_id) or {}
            for key in REPLY_FIELDS:
                if previous.get(key) not in (None, ""):
                    entry[key] = previous[key]
            items[message_id] = entry
        store["prompt_hash"] = current_hash
        save_store(store)

    out = {}
    for message in messages:
        message_id = str(message.get("id") or "")
        if message_id:
            out[message_id] = items.get(message_id) or normalize_category("other")
    return {
        "items": out,
        "prompt_hash": current_hash,
        "prompt_preview": _preview(used),
    }


def work_slug() -> str:
    mapping = category_map()
    if "work" in mapping:
        return "work"
    for slug, name in mapping.items():
        if slug == "work" or "work" in (name or "").lower():
            return slug
    return "work"


def tray_threads(category: Optional[str] = None) -> list[dict[str, Any]]:
    store = load_store()
    items = store.get("items") or {}
    tray = store.get("tray") if isinstance(store.get("tray"), list) else []
    seen: set[str] = set()
    threads: list[dict[str, Any]] = []
    for message in attach_categories(tray):
        message_category = message.get("category") or (
            items.get(str(message.get("id") or "")) or {}
        ).get("category")
        if category and message_category != category:
            continue
        thread_id = str(message.get("thread_id") or message.get("id") or "")
        if not thread_id or thread_id in seen:
            continue
        seen.add(thread_id)
        hit = items.get(str(message.get("id") or "")) or {}
        copy = dict(message)
        copy["thread_id"] = thread_id
        copy["needs_reply"] = bool(hit.get("needs_reply") or message.get("needs_reply"))
        copy["needs_reply_reason"] = hit.get("needs_reply_reason") or message.get("needs_reply_reason") or ""
        copy["reply_draft_id"] = hit.get("reply_draft_id") or message.get("reply_draft_id") or ""
        threads.append(copy)
    return threads


def work_threads() -> list[dict[str, Any]]:
    return tray_threads(work_slug())


def update_reply_flags(
    message_ids: list[str],
    thread_id: Optional[str] = None,
    needs_reply: Optional[bool] = None,
    needs_reply_reason: Optional[str] = None,
    reply_draft_id: Optional[str] = None,
    notion_page_id: Optional[str] = None,
    notion_url: Optional[str] = None,
    todo_title: Optional[str] = None,
    task_needed: Optional[bool] = None,
    task_details: Optional[str] = None,
    deadline: Optional[str] = None,
    importance: Optional[str] = None,
    additional_details: Optional[str] = None,
) -> dict[str, Any]:
    store = load_store()
    items: dict[str, Any] = store.setdefault("items", {})
    ids = [str(mid) for mid in message_ids if mid]
    if thread_id:
        for message in store.get("tray") or []:
            if str(message.get("thread_id") or "") == str(thread_id):
                mid = str(message.get("id") or "")
                if mid and mid not in ids:
                    ids.append(mid)
        for mid, hit in items.items():
            if str(hit.get("thread_id") or "") == str(thread_id) and mid not in ids:
                ids.append(mid)
    for message_id in ids:
        entry = dict(items.get(message_id) or {})
        if needs_reply is not None:
            entry["needs_reply"] = bool(needs_reply)
        if needs_reply_reason is not None:
            entry["needs_reply_reason"] = needs_reply_reason
        if reply_draft_id is not None:
            entry["reply_draft_id"] = reply_draft_id
        if notion_page_id is not None:
            entry["notion_page_id"] = notion_page_id
        if notion_url is not None:
            entry["notion_url"] = notion_url
        if todo_title is not None:
            entry["todo_title"] = todo_title
        if task_needed is not None:
            entry["task_needed"] = bool(task_needed)
        if task_details is not None:
            entry["task_details"] = task_details
        if deadline is not None:
            entry["deadline"] = deadline
        if importance is not None:
            entry["importance"] = importance
        if additional_details is not None:
            entry["additional_details"] = additional_details
        if thread_id:
            entry["thread_id"] = thread_id
        if "category" not in entry:
            entry.update(normalize_category("work"))
        items[message_id] = entry
    tray = store.get("tray") if isinstance(store.get("tray"), list) else []
    for row in tray:
        row_id = str(row.get("id") or "")
        same_thread = thread_id and str(row.get("thread_id") or "") == str(thread_id)
        if row_id in ids or same_thread:
            if needs_reply is not None:
                row["needs_reply"] = bool(needs_reply)
            if needs_reply_reason is not None:
                row["needs_reply_reason"] = needs_reply_reason
            if reply_draft_id is not None:
                row["reply_draft_id"] = reply_draft_id
            if notion_page_id is not None:
                row["notion_page_id"] = notion_page_id
            if notion_url is not None:
                row["notion_url"] = notion_url
            if todo_title is not None:
                row["todo_title"] = todo_title
            if task_needed is not None:
                row["task_needed"] = bool(task_needed)
            if task_details is not None:
                row["task_details"] = task_details
            if deadline is not None:
                row["deadline"] = deadline
            if importance is not None:
                row["importance"] = importance
            if additional_details is not None:
                row["additional_details"] = additional_details
    store["tray"] = tray
    save_store(store)
    return cached_items()


def update_thread_category(
    message_ids: list[str],
    category: str,
    thread_id: Optional[str] = None,
) -> dict[str, Any]:
    store = load_store()
    items: dict[str, Any] = store.setdefault("items", {})
    normalized = normalize_category(category)
    ids = [str(mid) for mid in message_ids if mid]
    for message_id in ids:
        entry = dict(items.get(message_id) or {})
        entry.update(normalized)
        if thread_id:
            entry["thread_id"] = thread_id
        items[message_id] = entry
    tray = store.get("tray") if isinstance(store.get("tray"), list) else []
    for row in tray:
        row_id = str(row.get("id") or "")
        same_thread = thread_id and str(row.get("thread_id") or "") == str(thread_id)
        if row_id in ids or same_thread:
            row["category"] = normalized["category"]
            row["category_label"] = normalized["label"]
    store["tray"] = tray
    save_store(store)
    return cached_items()
