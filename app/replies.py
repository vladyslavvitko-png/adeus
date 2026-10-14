from __future__ import annotations

import json
import re
from typing import Any, Optional

from googleapiclient.errors import HttpError

from . import auth, classify, deepseek, gmail_client, llm, notion_client, settings

DETECT_SYSTEM = (
    "You decide whether a Gmail thread needs a reply or another concrete action. "
    "Read the plain-text thread only. Do not invent facts. "
    'Reply with JSON only, shaped as {"needs_reply":false,"reason":"...",'
    '"todo_needed":false,"todo_title":"","todo_details":""}.'
)
DRAFT_SYSTEM = (
    "You write a short draft reply from the mailbox owner. "
    "Plain text only. No subject line, no markdown, no HTML, no quoted thread. "
    "Sound like a concise professional email. Do not invent facts, meetings, or promises."
)
ACCOUNT_ANALYSIS_SYSTEM = (
    "Classify one plain-text Gmail thread, then extract a task only when it belongs "
    "to the configured work category. Use only the provided thread text and never "
    "invent facts. Reply with JSON only, shaped as "
    '{"category":"work","needs_reply":false,"task_needed":false,"task_title":"","reason":"",'
    '"task_details":"","deadline":"","importance":"","additional_details":""}. '
    "The reason must explain why the task is or is not needed."
)


class ReplyScanError(RuntimeError):
    pass


def _parse_object(text: str) -> dict[str, Any]:
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
            raise ReplyScanError("The model did not return JSON for the reply check.")
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ReplyScanError("The model did not return a JSON object.")
    return data


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"true", "yes", "1", "need", "needed", "reply"}


def _owner_email() -> str:
    profile = auth.profile() or {}
    return str(profile.get("email") or "").strip()


def _compose_error(exc: HttpError) -> ReplyScanError:
    resp = getattr(exc, "resp", None)
    status = getattr(exc, "status_code", None) or getattr(resp, "status", None)
    if status in (401, 403):
        return ReplyScanError(
            "Gmail blocked draft creation. Reconnect Gmail and allow compose access "
            "so Adeus can add drafts to threads."
        )
    return ReplyScanError("Gmail could not create a draft for this thread.")


def _detect(owner: str, plain: str) -> dict[str, Any]:
    user = (
        f"Mailbox owner: {owner or '(unknown)'}\n\n"
        "Decide if the owner still owes a reply. True only if someone asked them a "
        "question, requested action, or is waiting on them, and they have not already "
        "answered in this thread. False for FYI, newsletters, automated mail, "
        "completed conversations, or no response expected. Separately extract any "
        "unfinished concrete action for the owner, such as signing and returning a "
        "contract form. Do not create todos for FYI or vague information.\n\n"
        f"Thread:\n{plain}"
    )
    try:
        text = llm.chat(DETECT_SYSTEM, user, temperature=0.1, timeout=90.0, json_mode=True)
    except llm.LlmError as exc:
        raise ReplyScanError(str(exc)) from exc
    data = _parse_object(text)
    reason = str(data.get("reason") or data.get("why") or "").strip()
    return {
        "needs_reply": _truthy(data.get("needs_reply", data.get("needsReply"))),
        "reason": reason[:280],
        "todo_needed": _truthy(data.get("todo_needed", data.get("todoNeeded"))),
        "todo_title": str(data.get("todo_title") or "").strip()[:200],
        "todo_details": str(data.get("todo_details") or "").strip()[:500],
    }


def _draft_body(owner: str, plain: str) -> str:
    user = (
        f"Write a short reply from {owner or 'the mailbox owner'} in their voice. "
        "Keep it to a few sentences. Do not include a subject line.\n\n"
        f"Thread:\n{plain}"
    )
    try:
        text = llm.chat(DRAFT_SYSTEM, user, temperature=0.4, timeout=90.0)
    except llm.LlmError as exc:
        raise ReplyScanError(str(exc)) from exc
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:text|email)?\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    return body.strip()


def _reply_target(message: dict[str, Any], owner: str) -> dict[str, str]:
    inbound = gmail_client.last_inbound(message, owner)
    sender = inbound.get("from") or {}
    to_addr = (inbound.get("reply_to") or "").strip()
    if "<" in to_addr and ">" in to_addr:
        to_addr = to_addr[to_addr.find("<") + 1 : to_addr.find(">")].strip()
    if not to_addr:
        to_addr = (sender.get("email") or "").strip()
    if owner and to_addr.lower() == owner.lower():
        to_header = inbound.get("to") or ""
        match = re.search(r"[\w.+-]+@[\w.-]+", to_header)
        to_addr = match.group(0) if match else to_addr
    return {
        "to": to_addr,
        "subject": inbound.get("subject") or message.get("subject") or "",
        "in_reply_to": inbound.get("rfc_message_id") or "",
        "references": inbound.get("references") or "",
        "thread_id": str(inbound.get("thread_id") or message.get("thread_id") or ""),
    }


def _sync_notion_todo(
    message: dict[str, Any],
    thread_ids: list[str],
    thread_id: str,
    category: str,
    needs_reply: bool,
    reason: str,
    todo_needed: bool = False,
    todo_title: str = "",
    todo_details: str = "",
) -> dict[str, Any]:
    required = bool(needs_reply or todo_needed)
    if not required:
        return {"needed": False, "created": False, "page_id": "", "url": "", "error": ""}
    cached = classify.cached_items().get("items") or {}
    existing = next(
        (
            cached.get(message_id) or {}
            for message_id in thread_ids
            if (cached.get(message_id) or {}).get("notion_page_id")
        ),
        {},
    )
    if existing:
        return {
            "needed": True,
            "created": False,
            "page_id": existing.get("notion_page_id") or "",
            "url": existing.get("notion_url") or "",
            "error": "",
        }
    if not notion_client.configured():
        return {
            "needed": True,
            "created": False,
            "page_id": "",
            "url": "",
            "error": "Notion is not configured.",
        }
    subject = str(message.get("subject") or "(no subject)")
    title = (
        f"Reply: {subject}"
        if needs_reply
        else (todo_title or f"Email action: {subject}")
    )
    details = todo_details or reason
    sender = message.get("from") or {}
    sender_text = (
        f"{sender.get('name') or ''} <{sender.get('email') or ''}>".strip()
        if isinstance(sender, dict)
        else str(sender)
    )
    try:
        created = notion_client.create_todo(
            title=title,
            details=details,
            subject=subject,
            sender=sender_text,
            category=category,
            thread_id=thread_id,
        )
    except notion_client.NotionError as exc:
        return {
            "needed": True,
            "created": False,
            "page_id": "",
            "url": "",
            "error": str(exc),
        }
    page_id = created.get("page_id") or ""
    url = created.get("url") or ""
    classify.update_reply_flags(
        thread_ids,
        thread_id=thread_id,
        notion_page_id=page_id,
        notion_url=url,
        todo_title=title,
    )
    return {
        "needed": True,
        "created": bool(page_id),
        "page_id": page_id,
        "url": url,
        "error": "",
    }


def _draft_context(message_id: str) -> dict[str, Any]:
    message = classify.attach_categories([gmail_client.get_message(message_id)])[0]
    owner = _owner_email()
    thread_id = str(message.get("thread_id") or "")
    thread_ids = [
        str(part.get("id") or "")
        for part in (message.get("thread") or [message])
        if part.get("id")
    ]
    draft_id = str(message.get("reply_draft_id") or "")
    if not draft_id:
        draft_id = gmail_client.find_thread_draft(thread_id) or ""
    return {
        "message": message,
        "owner": owner,
        "thread_id": thread_id,
        "thread_ids": thread_ids,
        "draft_id": draft_id,
        "target": _reply_target(message, owner),
        "plain": gmail_client.thread_plain_text(message),
    }


def reply_draft(message_id: str) -> dict[str, Any]:
    context = _draft_context(message_id)
    draft_id = context["draft_id"]
    body = ""
    if draft_id:
        body = gmail_client.get_reply_draft(draft_id).get("body") or ""
        classify.update_reply_flags(
            context["thread_ids"],
            thread_id=context["thread_id"],
            reply_draft_id=draft_id,
        )
    return {
        "id": message_id,
        "thread_id": context["thread_id"],
        "draft_id": draft_id,
        "body": body,
        "can_draft": auth.can_create_drafts(),
    }


def _write_reply_draft(context: dict[str, Any], body: str) -> dict[str, Any]:
    target = context["target"]
    draft_id = context["draft_id"]
    if draft_id:
        saved = gmail_client.update_reply_draft(
            draft_id=draft_id,
            thread_id=target["thread_id"] or context["thread_id"],
            to_addr=target["to"],
            subject=target["subject"],
            body=body,
            in_reply_to=target["in_reply_to"] or None,
            references=target["references"] or None,
        )
    else:
        saved = gmail_client.create_reply_draft(
            thread_id=target["thread_id"] or context["thread_id"],
            to_addr=target["to"],
            subject=target["subject"],
            body=body,
            in_reply_to=target["in_reply_to"] or None,
            references=target["references"] or None,
        )
    draft_id = str(saved.get("draft_id") or draft_id)
    classify.update_reply_flags(
        context["thread_ids"],
        thread_id=context["thread_id"],
        needs_reply=True,
        reply_draft_id=draft_id,
    )
    return {
        "id": str(context["message"].get("id") or ""),
        "thread_id": context["thread_id"],
        "draft_id": draft_id,
        "body": body.strip(),
        "items": classify.cached_items().get("items") or {},
    }


def save_reply_draft(message_id: str, body: str) -> dict[str, Any]:
    if not auth.can_create_drafts():
        raise ReplyScanError("Reconnect Gmail and allow compose access before saving drafts.")
    text = (body or "").strip()
    if not text:
        raise ReplyScanError("Draft body cannot be empty.")
    return _write_reply_draft(_draft_context(message_id), text)


def regenerate_reply_draft(message_id: str) -> dict[str, Any]:
    if not auth.can_create_drafts():
        raise ReplyScanError("Reconnect Gmail and allow compose access before regenerating drafts.")
    context = _draft_context(message_id)
    plain = context["plain"]
    if not plain.strip():
        raise ReplyScanError("This thread has no plain text to use for a reply.")
    body = _draft_body(context["owner"], plain)
    return _write_reply_draft(context, body)


def account_threads(start_date: str) -> dict[str, Any]:
    thread_refs = gmail_client.list_thread_refs_since(start_date)
    analyzed_ids = classify.analyzed_message_ids()
    pending = [
        ref
        for ref in thread_refs
        if any(
            str(message_id) not in analyzed_ids
            for message_id in (ref.get("message_ids") or [])
        )
    ]
    return {
        "start_date": start_date,
        "count": len(thread_refs),
        "analysis_count": len(pending),
        "discovered": len(thread_refs),
        "already_analyzed": len(thread_refs) - len(pending),
        "thread_ids": [ref["thread_id"] for ref in pending],
        "can_draft": auth.can_create_drafts(),
        "provider": "deepseek",
    }


def _account_verdict(owner: str, plain: str) -> dict[str, Any]:
    categories = settings.classify_categories()
    definitions = "\n".join(
        f"- {item['slug']}: {item['definition']}" for item in categories
    )
    slugs = ", ".join(item["slug"] for item in categories)
    work = classify.work_slug()
    user = (
        f"Mailbox owner: {owner or '(unknown)'}\n"
        f"Allowed category slugs: {slugs}\n"
        f"The work category slug is: {work}\n\n"
        f"Category definitions:\n{definitions}\n\n"
        "Classify the whole thread into exactly one allowed category. Only when the "
        f"category is the work slug ({work}), apply this saved Task Extraction Prompt:\n"
        f"{settings.task_extraction_prompt()}\n\n"
        "For every non-work category, task_needed must be false. For work threads, "
        "set needs_reply only when the owner owes an unanswered response. A needed "
        "reply is also a task. Return a reason even when no task is needed. "
        "Extract dates verbatim; use an "
        "empty string when no deadline or importance is stated.\n\n"
        f"Thread:\n{plain}"
    )
    try:
        text = deepseek.chat(
            ACCOUNT_ANALYSIS_SYSTEM,
            user,
            temperature=0.1,
            timeout=120.0,
            json_mode=True,
        )
    except deepseek.DeepSeekError as exc:
        raise ReplyScanError(str(exc)) from exc
    data = _parse_object(text)
    category = classify.normalize_category(str(data.get("category") or "other"))
    is_work = category["category"] == work
    reason = str(data.get("reason") or data.get("why") or "").strip()[:500]
    needs_reply = is_work and _truthy(
        data.get("needs_reply", data.get("needsReply"))
    )
    task_needed = is_work and (
        needs_reply
        or _truthy(data.get("task_needed", data.get("todo_needed", data.get("taskNeeded"))))
    )
    task_title = str(data.get("task_title") or data.get("todo_title") or "").strip()[:200]
    task_details = str(data.get("task_details") or data.get("todo_details") or "").strip()[:1200]
    deadline = str(data.get("deadline") or "").strip()[:160]
    importance = str(data.get("importance") or data.get("priority") or "").strip()[:80]
    additional_details = str(data.get("additional_details") or "").strip()[:800]
    if is_work and not reason:
        reason = (
            "The thread contains a concrete unfinished action for the mailbox owner."
            if task_needed
            else "No concrete unfinished task for the mailbox owner was found."
        )
    return {
        "category": category["category"],
        "label": category["label"],
        "is_work": is_work,
        "needs_reply": needs_reply,
        "reason": reason,
        "task_needed": task_needed,
        "task_title": task_title,
        "task_details": task_details,
        "deadline": deadline,
        "importance": importance,
        "additional_details": additional_details,
    }


def analyze_account_thread(
    thread_id: str,
    create_draft: bool = False,
    message: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not settings.deepseek_api_key():
        raise ReplyScanError("Set DEEPSEEK_API_KEY in .env first.")
    if not auth.load_credentials():
        raise PermissionError("Not connected to Gmail.")
    message = message or gmail_client.get_thread(thread_id)
    plain = gmail_client.thread_plain_text(message)
    if not plain.strip():
        raise ReplyScanError("This thread has no plain text to analyze.")
    owner = _owner_email()
    verdict = _account_verdict(owner, plain)
    thread_ids = [
        str(part.get("id") or "")
        for part in (message.get("thread") or [message])
        if part.get("id")
    ]
    classify.update_thread_category(
        thread_ids,
        verdict["category"],
        thread_id=thread_id,
    )
    draft_id = ""
    draft_created = False
    draft_error = ""
    if verdict["needs_reply"] and create_draft:
        draft_id = gmail_client.find_thread_draft(thread_id) or ""
        if not draft_id and not auth.can_create_drafts():
            draft_error = "Gmail compose access is missing."
        elif not draft_id:
            try:
                body = _draft_body(owner, plain)
                target = _reply_target(message, owner)
                created = gmail_client.create_reply_draft(
                    thread_id=target["thread_id"] or thread_id,
                    to_addr=target["to"],
                    subject=target["subject"] or message.get("subject") or "",
                    body=body,
                    in_reply_to=target["in_reply_to"] or None,
                    references=target["references"] or None,
                )
                draft_id = str(created.get("draft_id") or "")
                draft_created = bool(draft_id)
            except HttpError as exc:
                draft_error = str(_compose_error(exc))
            except (RuntimeError, ValueError) as exc:
                draft_error = str(exc)
    if verdict["is_work"]:
        classify.update_reply_flags(
            thread_ids,
            thread_id=thread_id,
            needs_reply=verdict["needs_reply"],
            needs_reply_reason=verdict["reason"],
            reply_draft_id=draft_id or None,
            task_needed=verdict["task_needed"],
            task_details=verdict["task_details"],
            deadline=verdict["deadline"],
            importance=verdict["importance"],
            additional_details=verdict["additional_details"],
        )
    detail_lines = [f"Why this task was created: {verdict['reason']}", verdict["task_details"]]
    if verdict["deadline"]:
        detail_lines.append(f"Deadline: {verdict['deadline']}")
    if verdict["importance"]:
        detail_lines.append(f"Importance: {verdict['importance']}")
    if verdict["additional_details"]:
        detail_lines.append(f"Additional details: {verdict['additional_details']}")
    task_details = "\n".join(line for line in detail_lines if line).strip()
    notion = _sync_notion_todo(
        message=message,
        thread_ids=thread_ids,
        thread_id=thread_id,
        category=verdict["label"],
        needs_reply=verdict["needs_reply"],
        reason=verdict["reason"],
        todo_needed=verdict["task_needed"],
        todo_title=verdict["task_title"],
        todo_details=task_details,
    )
    return {
        "thread_id": thread_id,
        "message_id": str(message.get("id") or ""),
        "subject": message.get("subject") or "(no subject)",
        "category": verdict["category"],
        "label": verdict["label"],
        "is_work": verdict["is_work"],
        "needs_reply": verdict["needs_reply"],
        "task_needed": verdict["task_needed"],
        "task_title": verdict["task_title"],
        "task_details": verdict["task_details"],
        "deadline": verdict["deadline"],
        "importance": verdict["importance"],
        "additional_details": verdict["additional_details"],
        "reason": verdict["reason"],
        "draft_id": draft_id,
        "draft_created": draft_created,
        "draft_error": draft_error,
        "notion_created": notion["created"],
        "notion_page_id": notion["page_id"],
        "notion_url": notion["url"],
        "notion_error": notion["error"],
    }


def tray_candidates() -> dict[str, Any]:
    threads = classify.tray_threads()
    return {
        "can_draft": auth.can_create_drafts(),
        "owner": _owner_email(),
        "threads": [
            {
                "id": item.get("id"),
                "thread_id": item.get("thread_id"),
                "subject": item.get("subject"),
                "from": item.get("from"),
                "needs_reply": bool(item.get("needs_reply")),
                "needs_reply_reason": item.get("needs_reply_reason") or "",
                "reply_draft_id": item.get("reply_draft_id") or "",
            }
            for item in threads
        ],
        "count": len(threads),
    }


def scan_threads(message_ids: Optional[list[str]] = None) -> dict[str, Any]:
    if not settings.grok_api_key() and not settings.deepseek_api_key():
        raise ReplyScanError("Set GROK_API_KEY or DEEPSEEK_API_KEY in .env first.")
    if not auth.load_credentials():
        raise PermissionError("Not connected to Gmail.")
    owner = _owner_email()
    can_draft = auth.can_create_drafts()
    wanted = [str(mid) for mid in (message_ids or []) if mid]
    candidates = classify.tray_threads()
    if wanted:
        wanted_set = set(wanted)
        candidates = [item for item in candidates if str(item.get("id") or "") in wanted_set]
    scanned = 0
    flagged = 0
    drafts_created = 0
    notion_created = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for item in candidates:
        message_id = str(item.get("id") or "")
        thread_id = str(item.get("thread_id") or "")
        subject = item.get("subject") or "(no subject)"
        row: dict[str, Any] = {
            "id": message_id,
            "thread_id": thread_id,
            "subject": subject,
            "needs_reply": False,
            "reason": "",
            "draft_id": item.get("reply_draft_id") or "",
            "skipped": False,
        }
        try:
            if item.get("reply_draft_id") or (thread_id and gmail_client.thread_has_draft(thread_id)):
                skipped += 1
                scanned += 1
                row["skipped"] = True
                row["reason"] = "Already has a draft on this thread."
                if item.get("needs_reply") or item.get("reply_draft_id"):
                    row["needs_reply"] = True
                    flagged += 1
                    notion = _sync_notion_todo(
                        message=item,
                        thread_ids=[message_id],
                        thread_id=thread_id,
                        category=item.get("category_label") or item.get("category") or "Work",
                        needs_reply=True,
                        reason=item.get("needs_reply_reason") or row["reason"],
                    )
                    row["notion_created"] = notion["created"]
                    row["notion_error"] = notion["error"]
                    notion_created += int(notion["created"])
                results.append(row)
                continue

            message = gmail_client.get_message(message_id)
            thread_id = str(message.get("thread_id") or thread_id)
            thread_ids = [str(part.get("id") or "") for part in (message.get("thread") or [message])]
            plain = gmail_client.thread_plain_text(message)
            if not plain.strip():
                classify.update_reply_flags(
                    thread_ids,
                    thread_id=thread_id,
                    needs_reply=False,
                    needs_reply_reason="No plain text in this thread.",
                )
                scanned += 1
                row["reason"] = "No plain text in this thread."
                results.append(row)
                continue

            verdict = _detect(owner, plain)
            needs_reply = bool(verdict.get("needs_reply"))
            reason = verdict.get("reason") or ""
            draft_id = ""
            if needs_reply:
                flagged += 1
                if not can_draft:
                    reason = (reason + " Draft not created: reconnect Gmail for compose access.").strip()
                else:
                    body = _draft_body(owner, plain)
                    target = _reply_target(message, owner)
                    try:
                        created = gmail_client.create_reply_draft(
                            thread_id=target["thread_id"] or thread_id,
                            to_addr=target["to"],
                            subject=target["subject"] or subject,
                            body=body,
                            in_reply_to=target["in_reply_to"] or None,
                            references=target["references"] or None,
                        )
                        draft_id = created.get("draft_id") or ""
                        drafts_created += 1
                    except HttpError as exc:
                        raise _compose_error(exc) from exc
            classify.update_reply_flags(
                thread_ids,
                thread_id=thread_id,
                needs_reply=needs_reply,
                needs_reply_reason=reason,
                reply_draft_id=draft_id or None,
            )
            notion = _sync_notion_todo(
                message=message,
                thread_ids=thread_ids,
                thread_id=thread_id,
                category=item.get("category_label") or item.get("category") or "Email",
                needs_reply=needs_reply,
                reason=reason,
                todo_needed=bool(verdict.get("todo_needed")),
                todo_title=verdict.get("todo_title") or "",
                todo_details=verdict.get("todo_details") or "",
            )
            notion_created += int(notion["created"])
            scanned += 1
            row.update(
                {
                    "needs_reply": needs_reply,
                    "reason": reason,
                    "draft_id": draft_id,
                    "notion_created": notion["created"],
                    "notion_error": notion["error"],
                }
            )
        except ReplyScanError as exc:
            errors.append(f"{subject}: {exc}")
            row["reason"] = str(exc)
        except PermissionError:
            raise
        except HttpError as exc:
            err = _compose_error(exc)
            errors.append(f"{subject}: {err}")
            row["reason"] = str(err)
        except Exception as exc:
            errors.append(f"{subject}: {exc}")
            row["reason"] = str(exc)
        results.append(row)

    return {
        "provider": settings.preferred_llm(),
        "can_draft": can_draft,
        "scanned": scanned,
        "flagged": flagged,
        "drafts_created": drafts_created,
        "notion_created": notion_created,
        "skipped": skipped,
        "count": len(candidates),
        "results": results,
        "errors": errors[:8],
        "items": classify.cached_items().get("items") or {},
    }
