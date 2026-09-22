from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import analysis, gmail_client, replies, scheduler


class FullAnalysisTests(unittest.TestCase):
    def test_full_analysis_counts_results_and_continues_after_failure(self) -> None:
        reply_result = {
            "label": "Work",
            "is_work": True,
            "needs_reply": True,
            "task_needed": True,
            "thread_id": "thread-1",
            "subject": "Contract",
            "reason": "A signed contract is requested.",
            "task_title": "Sign contract",
            "task_details": "Sign and return it.",
            "deadline": "Friday",
            "importance": "High",
            "additional_details": "",
            "draft_id": "draft-1",
            "draft_created": True,
            "draft_error": "",
            "notion_created": True,
            "notion_url": "https://notion.so/task",
            "notion_error": "",
        }
        updates = []
        with patch("app.analysis.settings.deepseek_api_key", return_value="key"), patch(
            "app.analysis.auth.load_credentials", return_value=object()
        ), patch("app.analysis.notion_client.configured", return_value=True), patch(
            "app.analysis.gmail_client.list_thread_refs_lookback",
            return_value=[
                {"thread_id": "thread-1", "message_ids": ["message-1"]},
                {"thread_id": "thread-2", "message_ids": ["message-2"]},
            ],
        ), patch(
            "app.analysis.classify.analyzed_message_ids", return_value=set()
        ), patch(
            "app.analysis.gmail_client.get_thread",
            side_effect=[
                {"id": "message-1", "thread_id": "thread-1"},
                {"id": "message-2", "thread_id": "thread-2"},
            ],
        ), patch(
            "app.analysis.classify.save_tray"
        ), patch(
            "app.analysis.classify.cached_items",
            return_value={
                "tray": [
                    {
                        "id": "message-1",
                        "thread_id": "thread-1",
                        "category": "work",
                        "category_label": "Work",
                        "subject": "Contract",
                        "needs_reply": True,
                        "needs_reply_reason": "A signed contract is requested.",
                    }
                ]
            },
        ), patch(
            "app.analysis.replies.analyze_account_thread",
            side_effect=[reply_result, RuntimeError("bad thread")],
        ):
            result = analysis.run_full_analysis(
                lookback_hours=24,
                create_drafts=True,
                progress=updates.append,
            )
        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(result["threads_analyzed"], 1)
        self.assertEqual(result["needs_reply"], 1)
        self.assertEqual(result["drafts_created"], 1)
        self.assertEqual(result["tasks_created"], 1)
        self.assertEqual(result["categories"], {"Work": 1})
        self.assertEqual(len(result["reply_items"]), 1)
        self.assertIn("bad thread", result["errors"][0])
        self.assertGreaterEqual(len(updates), 3)

    def test_full_analysis_skips_previously_analyzed_messages(self) -> None:
        with patch("app.analysis.settings.deepseek_api_key", return_value="key"), patch(
            "app.analysis.auth.load_credentials", return_value=object()
        ), patch("app.analysis.notion_client.configured", return_value=True), patch(
            "app.analysis.gmail_client.list_thread_refs_lookback",
            return_value=[
                {"thread_id": "old-thread", "message_ids": ["old-message"]},
                {"thread_id": "new-thread", "message_ids": ["new-message"]},
            ],
        ), patch(
            "app.analysis.classify.analyzed_message_ids",
            return_value={"old-message"},
        ), patch(
            "app.analysis.gmail_client.get_thread",
            side_effect=[
                {"id": "old-message", "thread_id": "old-thread"},
                {"id": "new-message", "thread_id": "new-thread"},
            ],
        ) as fetch, patch(
            "app.analysis.classify.save_tray"
        ), patch(
            "app.analysis.replies.analyze_account_thread",
            return_value={
                "label": "Other",
                "is_work": False,
                "needs_reply": False,
                "draft_created": False,
                "notion_created": False,
                "task_needed": False,
            },
        ) as analyze:
            result = analysis.run_full_analysis(lookback_hours=24)
        self.assertEqual(result["discovered"], 2)
        self.assertEqual(result["skipped_analyzed"], 1)
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["analysis_total"], 1)
        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            ["old-thread", "new-thread"],
        )
        analyze.assert_called_once()

    def test_full_analysis_verdict_creates_reply_draft(self) -> None:
        message = {
            "id": "message-1",
            "thread_id": "thread-1",
            "subject": "Please confirm",
            "thread": [{"id": "message-1"}],
        }
        verdict = {
            "category": "work",
            "label": "Work",
            "is_work": True,
            "needs_reply": True,
            "task_needed": True,
            "task_title": "Reply with confirmation",
            "task_details": "Confirm the request.",
            "deadline": "",
            "importance": "",
            "additional_details": "",
            "reason": "The sender is waiting for confirmation.",
        }
        target = {
            "thread_id": "thread-1",
            "to": "sender@example.com",
            "subject": "Please confirm",
            "in_reply_to": "",
            "references": "",
        }
        notion = {
            "created": True,
            "page_id": "page-1",
            "url": "https://notion.so/page-1",
            "error": "",
        }
        with patch("app.replies.settings.deepseek_api_key", return_value="key"), patch(
            "app.replies.auth.load_credentials", return_value=object()
        ), patch("app.replies.auth.can_create_drafts", return_value=True), patch(
            "app.replies.gmail_client.get_thread", return_value=message
        ), patch(
            "app.replies.gmail_client.thread_plain_text", return_value="Plain thread"
        ), patch("app.replies.gmail_client.find_thread_draft", return_value=None), patch(
            "app.replies.gmail_client.create_reply_draft",
            return_value={"draft_id": "draft-1"},
        ) as create_draft, patch(
            "app.replies._owner_email", return_value="owner@example.com"
        ), patch("app.replies._account_verdict", return_value=verdict), patch(
            "app.replies._draft_body", return_value="Draft body"
        ), patch("app.replies._reply_target", return_value=target), patch(
            "app.replies._sync_notion_todo", return_value=notion
        ), patch("app.replies.classify.update_thread_category"), patch(
            "app.replies.classify.update_reply_flags"
        ):
            result = replies.analyze_account_thread("thread-1", create_draft=True)
        self.assertTrue(result["draft_created"])
        self.assertEqual(result["draft_id"], "draft-1")
        create_draft.assert_called_once()

    def test_hourly_lookback_uses_timestamp_query(self) -> None:
        with patch("app.gmail_client._list_thread_ids", return_value=["thread"]) as listing:
            result = gmail_client.list_thread_ids_lookback(24)
        self.assertEqual(result, ["thread"])
        query = listing.call_args.args[0]
        self.assertRegex(query, r"^after:\d+$")

    def test_start_date_uses_exact_local_midnight_timestamp(self) -> None:
        local_zone = timezone(timedelta(hours=3))
        expected = int(
            datetime(2026, 8, 23, 0, 0, tzinfo=local_zone).timestamp()
        )
        with patch("app.gmail_client.get_localzone", return_value=local_zone), patch(
            "app.gmail_client._list_thread_ids", return_value=[]
        ) as listing:
            gmail_client.list_thread_ids_since("2026-08-23")
        listing.assert_called_once_with(f"after:{expected}")

    def test_full_analysis_honors_cancellation_between_threads(self) -> None:
        checks = iter([False, False, False, True])
        with patch("app.analysis.settings.deepseek_api_key", return_value="key"), patch(
            "app.analysis.auth.load_credentials", return_value=object()
        ), patch("app.analysis.notion_client.configured", return_value=True), patch(
            "app.analysis.gmail_client.list_thread_refs_lookback",
            return_value=[
                {"thread_id": "thread-1", "message_ids": ["message-1"]},
                {"thread_id": "thread-2", "message_ids": ["message-2"]},
            ],
        ), patch(
            "app.analysis.classify.analyzed_message_ids", return_value=set()
        ), patch(
            "app.analysis.gmail_client.get_thread",
            side_effect=[
                {"id": "message-1", "thread_id": "thread-1"},
                {"id": "message-2", "thread_id": "thread-2"},
            ],
        ), patch(
            "app.analysis.classify.save_tray"
        ), patch(
            "app.analysis.replies.analyze_account_thread",
            return_value={
                "label": "Other",
                "is_work": False,
                "needs_reply": False,
                "draft_created": False,
                "notion_created": False,
                "task_needed": False,
            },
        ) as analyze:
            result = analysis.run_full_analysis(
                lookback_hours=24,
                should_cancel=lambda: next(checks),
            )
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["processed"], 1)
        analyze.assert_called_once()


class SchedulerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store_patch = patch.object(scheduler, "STORE_PATH", root / "scheduler.json")
        self.lock_patch = patch.object(scheduler, "LOCK_PATH", root / ".analysis.lock")
        self.store_patch.start()
        self.lock_patch.start()
        scheduler._details.clear()

    def tearDown(self) -> None:
        self.lock_patch.stop()
        self.store_patch.stop()
        self.temp.cleanup()

    def test_config_validation_and_interrupted_recovery(self) -> None:
        status = scheduler.update_config(
            enabled=False,
            run_time="7:05",
            lookback_hours=48,
        )
        self.assertEqual(status["config"]["run_time"], "07:05")
        self.assertEqual(status["config"]["lookback_hours"], 48)
        store = scheduler._load_store()
        store["runs"] = [{"id": "r1", "status": "running"}]
        scheduler._save_store(store)
        scheduler._recover_interrupted_runs()
        recovered = scheduler._load_store()["runs"][0]
        self.assertEqual(recovered["status"], "interrupted")
        with self.assertRaises(ValueError):
            scheduler._validate_time("25:00")
        with self.assertRaises(ValueError):
            scheduler._validate_lookback(0)

    def test_cross_process_lock_rejects_second_holder(self) -> None:
        with scheduler._execution_lock() as first:
            self.assertTrue(first)
            with scheduler._execution_lock() as second:
                self.assertFalse(second)

    def test_persisted_history_keeps_summary_details(self) -> None:
        summary = {
            "threads_analyzed": 3,
            "drafts_created": 1,
            "tasks_created": 2,
            "reply_items": [{"subject": "Private subject"}],
        }
        public = scheduler._public_summary(summary)
        self.assertEqual(public["reply_items"], summary["reply_items"])
        self.assertEqual(public["threads_analyzed"], 3)

    def test_cancel_run_marks_active_record(self) -> None:
        store = scheduler._default_store()
        store["runs"] = [{"id": "r1", "status": "running"}]
        scheduler._save_store(store)
        run = scheduler.cancel_run("r1")
        self.assertEqual(run["status"], "cancelling")
        self.assertTrue(run["cancel_requested"])

    def test_z_enabled_schedule_has_next_run(self) -> None:
        scheduler.update_config(
            enabled=True,
            run_time="07:00",
            lookback_hours=24,
        )
        scheduler.start_scheduler()
        try:
            status = scheduler.status()
            self.assertTrue(status["next_run_at"])
            self.assertEqual(status["timezone"], str(scheduler.get_localzone()))
        finally:
            scheduler.stop_scheduler()


if __name__ == "__main__":
    unittest.main()
