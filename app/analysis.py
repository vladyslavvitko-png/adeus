from __future__ import annotations

from typing import Any, Callable, Optional

from . import auth, classify, gmail_client, notion_client, replies, settings

ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


class AnalysisError(RuntimeError):
    pass


def _notify(callback: Optional[ProgressCallback], summary: dict[str, Any]) -> None:
    if callback:
        callback(dict(summary))


def _summarize_downloaded_range(summary: dict[str, Any]) -> None:
    tray = classify.cached_items().get("tray") or []
    categories: dict[str, int] = {}
    reply_items: list[dict[str, Any]] = []
    task_items: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    rich_replies = {
        str(item.get("thread_id") or ""): item
        for item in (summary.get("reply_items") or [])
    }
    work_slug = classify.work_slug()
    work_threads = 0

    for message in tray:
        label = str(
            message.get("category_label")
            or message.get("category")
            or "Uncategorised"
        )
        categories[label] = int(categories.get(label) or 0) + 1
        if message.get("category") == work_slug:
            work_threads += 1
        thread_id = str(message.get("thread_id") or message.get("id") or "")
        if message.get("needs_reply"):
            stored = {
                "thread_id": thread_id,
                "subject": message.get("subject") or "(no subject)",
                "reason": message.get("needs_reply_reason") or "",
                "task_title": message.get("todo_title") or "",
                "task_details": message.get("task_details") or "",
                "deadline": message.get("deadline") or "",
                "importance": message.get("importance") or "",
                "additional_details": message.get("additional_details") or "",
                "draft_id": message.get("reply_draft_id") or "",
                "draft_created": False,
                "draft_error": "",
                "notion_url": message.get("notion_url") or "",
                "notion_error": "",
            }
            stored.update(rich_replies.get(thread_id) or {})
            reply_items.append(stored)
        notion_page_id = str(message.get("notion_page_id") or "")
        if notion_page_id and notion_page_id not in seen_tasks:
            seen_tasks.add(notion_page_id)
            task_items.append(
                {
                    "thread_id": thread_id,
                    "subject": message.get("subject") or "(no subject)",
                    "task_title": message.get("todo_title") or "Email task",
                    "reason": message.get("needs_reply_reason") or "",
                    "notion_url": message.get("notion_url") or "",
                }
            )

    summary["categories"] = categories
    summary["work_threads"] = work_threads
    summary["needs_reply"] = len(reply_items)
    summary["reply_items"] = reply_items
    summary["tasks_total"] = len(task_items)
    summary["tasks_existing"] = max(
        0,
        len(task_items) - int(summary.get("tasks_created") or 0),
    )
    summary["task_items"] = task_items


def run_full_analysis(
    *,
    start_date: Optional[str] = None,
    lookback_hours: Optional[int] = None,
    create_drafts: bool = True,
    reanalyze: bool = False,
    progress: Optional[ProgressCallback] = None,
    should_cancel: Optional[CancelCallback] = None,
) -> dict[str, Any]:
    if not settings.deepseek_api_key():
        raise AnalysisError("Set DEEPSEEK_API_KEY in .env before running analysis.")
    if not auth.load_credentials():
        raise AnalysisError("Connect Gmail before running analysis.")
    if not notion_client.configured():
        raise AnalysisError("Connect a Notion task database before running analysis.")
    if start_date:
        thread_refs = gmail_client.list_thread_refs_since(start_date)
        range_label = start_date
    elif lookback_hours is not None:
        hours = max(1, min(int(lookback_hours), 24 * 365))
        thread_refs = gmail_client.list_thread_refs_lookback(hours)
        range_label = f"Last {hours} hours"
    else:
        raise AnalysisError("Provide a start date or lookback period.")

    analyzed_ids = classify.analyzed_message_ids()
    pending_refs = [
        ref
        for ref in thread_refs
        if reanalyze
        or any(
            str(message_id) not in analyzed_ids
            for message_id in (ref.get("message_ids") or [])
        )
    ]
    summary: dict[str, Any] = {
        "range": range_label,
        "start_date": start_date or "",
        "lookback_hours": lookback_hours,
        "discovered": len(thread_refs),
        "skipped_analyzed": len(thread_refs) - len(pending_refs),
        "total": len(thread_refs),
        "analysis_total": len(pending_refs),
        "reanalyze": bool(reanalyze),
        "phase": "fetching",
        "fetched": 0,
        "processed": 0,
        "threads_analyzed": 0,
        "new_work_threads": 0,
        "new_needs_reply": 0,
        "work_threads": 0,
        "needs_reply": 0,
        "drafts_created": 0,
        "tasks_created": 0,
        "categories": {},
        "reply_items": [],
        "errors": [],
        "cancelled": False,
    }
    _notify(progress, summary)

    downloaded: list[tuple[str, dict[str, Any]]] = []
    for ref in thread_refs:
        thread_id = str(ref.get("thread_id") or "")
        if should_cancel and should_cancel():
            summary["cancelled"] = True
            _notify(progress, summary)
            break
        try:
            message = gmail_client.get_thread(thread_id)
            downloaded.append((thread_id, message))
            summary["fetched"] = len(downloaded)
        except Exception as exc:
            summary["errors"].append(f"Could not fetch thread {thread_id}: {exc}")
        _notify(progress, summary)

    if summary["cancelled"]:
        return summary

    classify.save_tray(
        [message for _, message in downloaded],
        label="ALL",
        query="",
    )
    summary["phase"] = "analyzing"
    _notify(progress, summary)

    pending_thread_ids = {
        str(ref.get("thread_id") or "")
        for ref in pending_refs
    }
    analysis_queue = [
        (thread_id, message)
        for thread_id, message in downloaded
        if thread_id in pending_thread_ids
    ]
    for index, (thread_id, message) in enumerate(analysis_queue):
        if should_cancel and should_cancel():
            summary["cancelled"] = True
            _notify(progress, summary)
            break
        try:
            result = replies.analyze_account_thread(
                thread_id,
                create_draft=create_drafts,
                message=message,
            )
            summary["threads_analyzed"] += 1
            label = str(result.get("label") or result.get("category") or "Uncategorised")
            categories = summary["categories"]
            categories[label] = int(categories.get(label) or 0) + 1
            if result.get("is_work"):
                summary["work_threads"] += 1
                summary["new_work_threads"] += 1
            if result.get("needs_reply"):
                summary["needs_reply"] += 1
                summary["new_needs_reply"] += 1
                summary["reply_items"].append(
                    {
                        key: result.get(key)
                        for key in (
                            "thread_id",
                            "subject",
                            "reason",
                            "task_title",
                            "task_details",
                            "deadline",
                            "importance",
                            "additional_details",
                            "draft_id",
                            "draft_created",
                            "draft_error",
                            "notion_url",
                            "notion_error",
                        )
                    }
                )
            if result.get("draft_created"):
                summary["drafts_created"] += 1
            if result.get("notion_created"):
                summary["tasks_created"] += 1
            if result.get("draft_error"):
                summary["errors"].append(
                    f"{result.get('subject') or thread_id}: {result['draft_error']}"
                )
            if result.get("task_needed") and result.get("notion_error"):
                summary["errors"].append(
                    f"{result.get('subject') or thread_id}: {result['notion_error']}"
                )
        except Exception as exc:
            summary["errors"].append(f"Thread {thread_id}: {exc}")
        summary["processed"] = index + 1
        _notify(progress, summary)

    if not summary["cancelled"]:
        _summarize_downloaded_range(summary)
        summary["phase"] = "completed"
        _notify(progress, summary)
    return summary
