from __future__ import annotations

import json
import re
from typing import Any, Optional

from .auth import ROOT

SETTINGS_PATH = ROOT / "settings.json"
KEY_FIELDS = ("grok_api_key", "deepseek_api_key", "notion_api_key")
MAX_CLASSIFY_LIMIT = 1000
MAX_CATEGORIES = 16
DEFAULT_TASK_EXTRACTION_PROMPT = (
    "For work email threads, decide whether the mailbox owner has a specific unfinished "
    "task. Create a task only when the thread contains a concrete request, commitment, "
    "deadline, approval, document, follow-up, or other action the owner must complete. "
    "Do not create tasks for newsletters, FYI messages, completed requests, vague ideas, "
    "or actions assigned to somebody else. Extract a concise task title, explain why it "
    "is needed, and preserve useful context including the deadline, importance, people, "
    "documents, links, and next steps. Never invent missing details."
)

DEFAULT_CATEGORIES = [
    {
        "slug": "invoice",
        "name": "Invoices",
        "definition": (
            "bills, invoices, payment due, tax documents, order confirmations, "
            "payment receipts, tickets already paid. A PDF named like an invoice, "
            "receipt, or order is a strong hint. Cloud/disk/memory/quota alerts are not invoices."
        ),
    },
    {
        "slug": "private",
        "name": "Private",
        "definition": (
            "one-to-one mail from a real person — family, friends, personal notes. "
            "Never private if unsubscribe or mailing_list is true, or if the mail is a "
            "blog, digest, newsletter, or subscription, even when it feels personal."
        ),
    },
    {
        "slug": "social",
        "name": "Social",
        "definition": (
            "marketing, sales, discounts, cold outreach, social networks, community "
            "notifications, forums, newsletters, digests, blog mailings, mailing lists, subscriptions"
        ),
    },
    {
        "slug": "travel",
        "name": "Travel",
        "definition": "flights, hotels, itineraries, boarding passes",
    },
    {
        "slug": "work",
        "name": "Work",
        "definition": "colleagues, workplace tools, jobs, professional mail that is not a newsletter",
    },
    {
        "slug": "other",
        "name": "Other",
        "definition": (
            "anything that does not fit, including system alerts "
            "(storage full, no memory left, backups, monitoring)"
        ),
    },
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:40] or "category"


def normalize_categories(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        return [dict(item) for item in DEFAULT_CATEGORIES]
    used: set[str] = set()
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:60]
        definition = str(item.get("definition") or "").strip()[:800]
        if not name and not definition:
            continue
        if not name:
            name = "Category"
        slug = _slugify(str(item.get("slug") or "") or name)
        base = slug
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        out.append({"slug": slug, "name": name, "definition": definition or name})
        if len(out) >= MAX_CATEGORIES:
            break
    return out or [dict(item) for item in DEFAULT_CATEGORIES]


def build_classify_prompt(categories: Optional[list[dict[str, str]]] = None) -> str:
    cats = categories if categories is not None else classify_categories()
    slugs = ", ".join(item["slug"] for item in cats)
    lines = [
        "Classify each email into exactly one category. Use the subject, sender, snippet, and the flags Adeus adds (unsubscribe, mailing_list, pdf, files).",
        "",
        f"Return one of these slugs only: {slugs}.",
        "",
    ]
    for item in cats:
        lines.append(f"- {item['slug']}: {item['definition']}")
    lines.append("")
    lines.append("If two labels could apply, pick the more specific one.")
    return "\n".join(lines)


def load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    rebuilt_from_categories = False
    if "classify_categories" in updates and updates["classify_categories"] is not None:
        cats = normalize_categories(updates["classify_categories"])
        current["classify_categories"] = cats
        current["classify_prompt"] = build_classify_prompt(cats)
        rebuilt_from_categories = True
    if "classify_limit" in updates and updates["classify_limit"] is not None:
        try:
            current["classify_limit"] = max(1, min(int(updates["classify_limit"]), MAX_CLASSIFY_LIMIT))
        except (TypeError, ValueError):
            pass
    for key, value in updates.items():
        if value is None or key in ("classify_categories", "classify_limit"):
            continue
        if key in KEY_FIELDS and isinstance(value, str) and value.strip() == "":
            current.pop(key, None)
            continue
        if key == "classify_prompt":
            if rebuilt_from_categories:
                continue
            if isinstance(value, str) and value.strip() == "":
                cats = [dict(item) for item in DEFAULT_CATEGORIES]
                current["classify_categories"] = cats
                current["classify_prompt"] = build_classify_prompt(cats)
            elif isinstance(value, str):
                current[key] = value
            continue
        if key == "task_extraction_prompt":
            if isinstance(value, str):
                current[key] = value.strip()[:6000] or DEFAULT_TASK_EXTRACTION_PROMPT
            continue
        current[key] = value.strip() if isinstance(value, str) else value
    SETTINGS_PATH.write_text(json.dumps(current, indent=2) + "\n")
    try:
        SETTINGS_PATH.chmod(0o600)
    except OSError:
        pass
    return public_settings()


def _key(name: str) -> Optional[str]:
    value = (load_settings().get(name) or "").strip()
    return value or None


def grok_api_key() -> Optional[str]:
    return _key("grok_api_key")


def deepseek_api_key() -> Optional[str]:
    return _key("deepseek_api_key")


def notion_api_key() -> Optional[str]:
    return _key("notion_api_key")


def notion_database_id() -> Optional[str]:
    value = (load_settings().get("notion_database_id") or "").strip()
    return value or None


def grok_model() -> str:
    return (load_settings().get("grok_model") or "grok-4").strip() or "grok-4"


def deepseek_model() -> str:
    return (load_settings().get("deepseek_model") or "deepseek-chat").strip() or "deepseek-chat"


def classify_categories() -> list[dict[str, str]]:
    return normalize_categories(load_settings().get("classify_categories"))


def classify_limit() -> int:
    raw = load_settings().get("classify_limit")
    try:
        return max(1, min(int(raw), MAX_CLASSIFY_LIMIT))
    except (TypeError, ValueError):
        return MAX_CLASSIFY_LIMIT


def classify_prompt() -> str:
    stored = load_settings().get("classify_prompt")
    if isinstance(stored, str) and stored.strip():
        return stored
    return build_classify_prompt()


def task_extraction_prompt() -> str:
    stored = load_settings().get("task_extraction_prompt")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return DEFAULT_TASK_EXTRACTION_PROMPT


def preferred_llm() -> str:
    choice = (load_settings().get("preferred_llm") or "").strip()
    if choice == "deepseek" and deepseek_api_key():
        return "deepseek"
    if choice == "grok" and grok_api_key():
        return "grok"
    if grok_api_key():
        return "grok"
    if deepseek_api_key():
        return "deepseek"
    return "grok"


def _hint(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return key[:4] + "…" + key[-4:]


def public_settings() -> dict[str, Any]:
    grok = grok_api_key()
    deepseek = deepseek_api_key()
    notion = notion_api_key()
    return {
        "grok_configured": bool(grok),
        "grok_key_hint": _hint(grok) if grok else "",
        "grok_model": grok_model(),
        "deepseek_configured": bool(deepseek),
        "deepseek_key_hint": _hint(deepseek) if deepseek else "",
        "deepseek_model": deepseek_model(),
        "notion_configured": bool(notion and notion_database_id()),
        "notion_key_hint": _hint(notion) if notion else "",
        "notion_database_id": notion_database_id() or "",
        "preferred_llm": preferred_llm(),
        "classify_prompt": classify_prompt(),
        "task_extraction_prompt": task_extraction_prompt(),
        "classify_limit": classify_limit(),
        "classify_categories": classify_categories(),
    }
