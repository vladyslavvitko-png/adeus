from __future__ import annotations

import copy
import fcntl
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Iterator, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from tzlocal import get_localzone

from . import analysis
from .auth import ROOT

STORE_PATH = ROOT / "scheduler.json"
LOCK_PATH = ROOT / ".analysis.lock"
JOB_ID = "adeus-daily-analysis"
DEFAULT_CONFIG = {
    "enabled": False,
    "run_time": "07:00",
    "lookback_hours": 24,
}

_state_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="adeus-analysis")
_scheduler = BackgroundScheduler(timezone=get_localzone())
_details: dict[str, dict[str, Any]] = {}
_started = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_store() -> dict[str, Any]:
    return {"config": dict(DEFAULT_CONFIG), "runs": []}


def _load_store() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return _default_store()
    try:
        data = json.loads(STORE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return _default_store()
    if not isinstance(data, dict):
        return _default_store()
    config = dict(DEFAULT_CONFIG)
    if isinstance(data.get("config"), dict):
        config.update(data["config"])
    runs = data.get("runs") if isinstance(data.get("runs"), list) else []
    return {"config": config, "runs": runs}


def _save_store(store: dict[str, Any]) -> None:
    temporary = STORE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(store, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(STORE_PATH)


def _public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(summary)


def _update_run(run_id: str, **updates: Any) -> dict[str, Any]:
    with _state_lock:
        store = _load_store()
        run = next(
            (item for item in store["runs"] if str(item.get("id")) == run_id),
            None,
        )
        if run is None:
            raise KeyError(run_id)
        run.update(updates)
        _save_store(store)
        return copy.deepcopy(run)


def _recover_interrupted_runs() -> None:
    with _state_lock:
        store = _load_store()
        changed = False
        for run in store["runs"]:
            if run.get("status") in {"queued", "running", "cancelling"}:
                run["status"] = "interrupted"
                run["finished_at"] = _now()
                run["error"] = "Adeus stopped before this run finished."
                changed = True
        if changed:
            _save_store(store)


@contextmanager
def _execution_lock() -> Iterator[bool]:
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _execute_run(
    run_id: str,
    *,
    start_date: Optional[str],
    lookback_hours: Optional[int],
    create_drafts: bool,
    reanalyze: bool,
) -> None:
    with _execution_lock() as acquired:
        if not acquired:
            _update_run(
                run_id,
                status="skipped",
                finished_at=_now(),
                error="Another full analysis is already running.",
            )
            return
        started = monotonic()
        _update_run(run_id, status="running", started_at=_now())

        def on_progress(summary: dict[str, Any]) -> None:
            _details[run_id] = copy.deepcopy(summary)
            _update_run(run_id, summary=_public_summary(summary))

        def should_cancel() -> bool:
            with _state_lock:
                store = _load_store()
                run = next(
                    (item for item in store["runs"] if str(item.get("id")) == run_id),
                    {},
                )
                return bool(run.get("cancel_requested"))

        try:
            summary = analysis.run_full_analysis(
                start_date=start_date,
                lookback_hours=lookback_hours,
                create_drafts=create_drafts,
                reanalyze=reanalyze,
                progress=on_progress,
                should_cancel=should_cancel,
            )
            _details[run_id] = copy.deepcopy(summary)
            _update_run(
                run_id,
                status="cancelled" if summary.get("cancelled") else "completed",
                finished_at=_now(),
                duration_seconds=round(monotonic() - started, 2),
                summary=_public_summary(summary),
                error="",
            )
        except Exception as exc:
            _update_run(
                run_id,
                status="failed",
                finished_at=_now(),
                duration_seconds=round(monotonic() - started, 2),
                error=str(exc),
            )


def start_run(
    *,
    trigger: str,
    start_date: Optional[str] = None,
    lookback_hours: Optional[int] = None,
    create_drafts: bool = True,
    reanalyze: bool = False,
) -> dict[str, Any]:
    if not start_date and lookback_hours is None:
        raise ValueError("Provide a start date or lookback period.")
    run = {
        "id": uuid.uuid4().hex,
        "trigger": trigger,
        "status": "queued",
        "created_at": _now(),
        "started_at": "",
        "finished_at": "",
        "duration_seconds": 0,
        "start_date": start_date or "",
        "lookback_hours": lookback_hours,
        "create_drafts": bool(create_drafts),
        "reanalyze": bool(reanalyze),
        "summary": {},
        "error": "",
        "cancel_requested": False,
    }
    with _state_lock:
        store = _load_store()
        if any(
            item.get("status") in {"queued", "running", "cancelling"}
            for item in store["runs"]
        ):
            raise RuntimeError("A full analysis is already running.")
        store["runs"].insert(0, run)
        _save_store(store)
    _executor.submit(
        _execute_run,
        run["id"],
        start_date=start_date,
        lookback_hours=lookback_hours,
        create_drafts=create_drafts,
        reanalyze=reanalyze,
    )
    return copy.deepcopy(run)


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    with _state_lock:
        store = _load_store()
        run = next(
            (item for item in store["runs"] if str(item.get("id")) == run_id),
            None,
        )
        if run is None:
            return None
        out = copy.deepcopy(run)
        if run_id in _details:
            out["summary"] = copy.deepcopy(_details[run_id])
        return out


def cancel_run(run_id: str) -> Optional[dict[str, Any]]:
    with _state_lock:
        store = _load_store()
        run = next(
            (item for item in store["runs"] if str(item.get("id")) == run_id),
            None,
        )
        if run is None:
            return None
        if run.get("status") not in {"queued", "running", "cancelling"}:
            return copy.deepcopy(run)
        run["cancel_requested"] = True
        run["status"] = "cancelling"
        _save_store(store)
        return copy.deepcopy(run)


def _validate_time(value: str) -> str:
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Run time must use HH:MM.") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Run time must use a valid 24-hour time.")
    return f"{hour:02d}:{minute:02d}"


def _validate_lookback(value: Any) -> int:
    try:
        hours = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Lookback must be a whole number of hours.") from exc
    if not 1 <= hours <= 24 * 365:
        raise ValueError("Lookback must be between 1 and 8,760 hours.")
    return hours


def _scheduled_run() -> None:
    config = _load_store()["config"]
    if config.get("enabled"):
        try:
            start_run(
                trigger="scheduled",
                lookback_hours=_validate_lookback(config.get("lookback_hours")),
                create_drafts=True,
            )
        except RuntimeError:
            pass


def _apply_schedule() -> None:
    if not _started:
        return
    existing = _scheduler.get_job(JOB_ID)
    if existing:
        _scheduler.remove_job(JOB_ID)
    config = _load_store()["config"]
    if not config.get("enabled"):
        return
    run_time = _validate_time(config.get("run_time"))
    hour, minute = (int(part) for part in run_time.split(":"))
    _scheduler.add_job(
        _scheduled_run,
        CronTrigger(
            hour=hour,
            minute=minute,
            timezone=get_localzone(),
        ),
        id=JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60 * 60,
    )


def update_config(
    *,
    enabled: bool,
    run_time: str,
    lookback_hours: int,
) -> dict[str, Any]:
    config = {
        "enabled": bool(enabled),
        "run_time": _validate_time(run_time),
        "lookback_hours": _validate_lookback(lookback_hours),
    }
    with _state_lock:
        store = _load_store()
        store["config"] = config
        _save_store(store)
    _apply_schedule()
    return status()


def status() -> dict[str, Any]:
    with _state_lock:
        store = _load_store()
    runs = copy.deepcopy(store["runs"])
    last_scheduled = next(
        (run for run in runs if run.get("trigger") == "scheduled"),
        None,
    )
    active = next(
        (
            run
            for run in runs
            if run.get("status") in {"queued", "running", "cancelling"}
        ),
        None,
    )
    if active and active.get("id") in _details:
        active["summary"] = copy.deepcopy(_details[active["id"]])
    job = _scheduler.get_job(JOB_ID) if _started else None
    return {
        "config": copy.deepcopy(store["config"]),
        "timezone": str(get_localzone()),
        "next_run_at": job.next_run_time.isoformat() if job and job.next_run_time else "",
        "active_run": active,
        "last_scheduled_run": last_scheduled,
        "runs": runs,
    }


def start_scheduler() -> None:
    global _started
    with _state_lock:
        if _started:
            return
        _recover_interrupted_runs()
        _scheduler.start()
        _started = True
        _apply_schedule()


def stop_scheduler() -> None:
    global _started
    with _state_lock:
        if not _started:
            return
        _scheduler.shutdown(wait=False)
        _started = False

