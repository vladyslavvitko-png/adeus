from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from googleapiclient.errors import HttpError

from pydantic import BaseModel, Field

from . import auth, classify, deepseek, gmail_client, grok, replies, scheduler, settings

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

STATIC_DIR = auth.ROOT / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler.start_scheduler()
    try:
        yield
    finally:
        scheduler.stop_scheduler()


app = FastAPI(
    title="Adeus",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CategoryItem(BaseModel):
    name: str = ""
    definition: str = ""
    slug: Optional[str] = None


class SettingsUpdate(BaseModel):
    grok_api_key: Optional[str] = None
    grok_model: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    deepseek_model: Optional[str] = None
    notion_api_key: Optional[str] = None
    notion_database_id: Optional[str] = None
    preferred_llm: Optional[str] = None
    classify_prompt: Optional[str] = None
    task_extraction_prompt: Optional[str] = None
    classify_limit: Optional[int] = None
    classify_categories: Optional[List[CategoryItem]] = None


class ClassifyRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    force: bool = False
    prompt: Optional[str] = None


class TraySave(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    label: str = "INBOX"
    query: str = ""


class LlmAsk(BaseModel):
    question: str = Field(min_length=1)
    message_id: Optional[str] = None
    provider: Optional[str] = None


class ScanRequest(BaseModel):
    message_ids: list[str] = Field(default_factory=list)


class DraftUpdate(BaseModel):
    body: str = Field(min_length=1)


class AccountThreadRequest(BaseModel):
    thread_id: str = Field(min_length=1)


class BulkPreviewRequest(BaseModel):
    query: str = Field(min_length=1)


class BulkActionRequest(BaseModel):
    action: str = Field(pattern="^(archive|trash)$")
    ids: List[str] = Field(default_factory=list)
    query: Optional[str] = None


class SchedulerUpdate(BaseModel):
    enabled: bool = False
    run_time: str = "07:00"
    lookback_hours: int = Field(default=24, ge=1, le=8760)


class FullAnalysisRequest(BaseModel):
    start_date: Optional[str] = None
    lookback_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    create_drafts: bool = True
    reanalyze: bool = False


def _gmail_http_error(exc: HttpError) -> HTTPException:
    status = exc.status_code or 500
    detail = "Gmail request failed."
    try:
        payload = exc.error_details or []
        if payload:
            detail = payload[0].get("message") or detail
        elif exc.content:
            detail = exc.content.decode("utf-8", errors="replace")
    except Exception:
        pass
    if status == 403:
        detail = (
            "Gmail API access was denied. Enable the Gmail API for this Google Cloud "
            "project and add your Google account as a test user on the OAuth consent screen."
        )
    return HTTPException(status_code=status, detail=detail)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status():
    try:
        info = auth.client_info()
    except FileNotFoundError as exc:
        return JSONResponse({"connected": False, "error": str(exc)}, status_code=500)
    profile = auth.profile()
    return {
        "connected": profile is not None,
        "profile": profile,
        "client": info,
        "gmail_compose": auth.can_create_drafts() if profile else False,
        "gmail_modify": auth.can_modify() if profile else False,
        "grok": settings.public_settings(),
    }


@app.post("/api/auth/login")
def login(force: bool = Query(False)):
    try:
        auth.run_desktop_login(force=force)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Google sign-in did not finish: {exc}",
        ) from exc
    return {"ok": True, "profile": auth.profile()}


@app.post("/api/auth/logout")
def logout():
    auth.clear_credentials()
    return {"ok": True}


@app.get("/api/labels")
def labels():
    try:
        return {"labels": gmail_client.list_labels()}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/api/messages")
def messages(
    label: str = Query("INBOX"),
    q: str = Query(""),
    page_token: Optional[str] = Query(None),
):
    try:
        data = gmail_client.list_messages(label=label, query=q, page_token=page_token)
        data["messages"] = classify.attach_categories(data["messages"])
        return data
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/api/messages/recent")
def recent_messages(
    label: str = Query("INBOX"),
    q: str = Query(""),
    limit: int = Query(1000),
):
    try:
        data = gmail_client.list_recent_messages(label=label, query=q, limit=min(limit, 1000))
        data["messages"] = classify.attach_categories(data["messages"])
        return data
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/messages/bulk/preview")
def bulk_preview(payload: BulkPreviewRequest):
    """Count how many messages a Gmail query would affect, without acting."""
    try:
        result = gmail_client.list_matching_message_ids(payload.query)
        return {"count": result["count"], "capped": result["capped"]}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/messages/bulk")
def bulk_action(payload: BulkActionRequest):
    if not auth.can_modify():
        raise HTTPException(
            status_code=403,
            detail=(
                "Gmail modify permission is missing. Open Settings and use "
                "Reconnect Gmail to grant archive/delete access."
            ),
        )
    try:
        capped = False
        ids = [item for item in payload.ids if item]
        if not ids and payload.query:
            resolved = gmail_client.list_matching_message_ids(payload.query)
            ids = resolved["ids"]
            capped = resolved["capped"]
        if not ids:
            raise HTTPException(
                status_code=400,
                detail="Nothing to act on: no message ids or matching query results.",
            )
        if payload.action == "archive":
            result = gmail_client.archive_messages(ids)
        else:
            result = gmail_client.trash_messages(ids)
        return {
            "action": payload.action,
            "count": result["count"],
            "failed": result.get("failed", 0),
            "capped": capped,
            "ids": ids,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/api/settings")
def get_settings():
    return settings.public_settings()


@app.put("/api/settings")
def put_settings(payload: SettingsUpdate):
    return settings.save_settings(payload.model_dump())


@app.get("/api/classifications")
def get_classifications():
    return classify.cached_items()


@app.put("/api/classifications/tray")
def put_classification_tray(payload: TraySave):
    return classify.save_tray(payload.messages, label=payload.label, query=payload.query)


@app.post("/api/classify")
def classify_mail(payload: ClassifyRequest):
    try:
        return classify.classify_messages(
            payload.messages,
            force=payload.force,
            prompt=payload.prompt,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except deepseek.DeepSeekError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/grok/summarize/{message_id}")
def grok_summarize(message_id: str):
    try:
        return {"text": grok.summarize(message_id)}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except grok.GrokError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/grok/ask")
def grok_ask(payload: LlmAsk):
    try:
        return {"text": grok.complete(payload.question, message_id=payload.message_id)}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except grok.GrokError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/deepseek/summarize/{message_id}")
def deepseek_summarize(message_id: str):
    try:
        return {"text": deepseek.summarize(message_id)}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except deepseek.DeepSeekError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/deepseek/ask")
def deepseek_ask(payload: LlmAsk):
    try:
        return {"text": deepseek.complete(payload.question, message_id=payload.message_id)}
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except deepseek.DeepSeekError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/api/replies/threads")
def reply_threads():
    try:
        return replies.tray_candidates()
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/api/replies/scan")
def reply_scan(payload: ScanRequest):
    try:
        return replies.scan_threads(payload.message_ids)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except replies.ReplyScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/api/replies/draft/{message_id}")
def get_reply_draft(message_id: str):
    try:
        return replies.reply_draft(message_id)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (ValueError, replies.ReplyScanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.put("/api/replies/draft/{message_id}")
def put_reply_draft(message_id: str, payload: DraftUpdate):
    try:
        return replies.save_reply_draft(message_id, payload.body)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (ValueError, replies.ReplyScanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/replies/draft/{message_id}/regenerate")
def regenerate_reply_draft(message_id: str):
    try:
        return replies.regenerate_reply_draft(message_id)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (ValueError, replies.ReplyScanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/api/account-analysis/threads")
def account_analysis_threads(start_date: str = Query(...)):
    try:
        return replies.account_threads(start_date)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.post("/api/account-analysis/thread")
def account_analysis_thread(payload: AccountThreadRequest):
    try:
        return replies.analyze_account_thread(payload.thread_id)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except (ValueError, replies.ReplyScanError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/api/scheduler")
def scheduler_status():
    return scheduler.status()


@app.put("/api/scheduler")
def put_scheduler(payload: SchedulerUpdate):
    try:
        return scheduler.update_config(
            enabled=payload.enabled,
            run_time=payload.run_time,
            lookback_hours=payload.lookback_hours,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/scheduler/run-now")
def scheduler_run_now():
    config = scheduler.status()["config"]
    try:
        return scheduler.start_run(
            trigger="manual",
            lookback_hours=int(config["lookback_hours"]),
            create_drafts=True,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/analysis/runs")
def start_analysis_run(payload: FullAnalysisRequest):
    try:
        return scheduler.start_run(
            trigger="manual",
            start_date=payload.start_date,
            lookback_hours=payload.lookback_hours,
            create_drafts=payload.create_drafts,
            reanalyze=payload.reanalyze,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/analysis/runs/{run_id}")
def analysis_run(run_id: str):
    run = scheduler.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    return run


@app.post("/api/analysis/runs/{run_id}/cancel")
def cancel_analysis_run(run_id: str):
    run = scheduler.cancel_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found.")
    return run


@app.get("/api/messages/{message_id}")
def message(message_id: str):
    try:
        item = gmail_client.get_message(message_id)
        return classify.attach_categories([item])[0]
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except HttpError as exc:
        raise _gmail_http_error(exc) from exc


@app.get("/health")
def health():
    return {"ok": True}
