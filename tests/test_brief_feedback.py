import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

COLLECTORS = Path(__file__).resolve().parents[1] / "collectors"
sys.path.insert(0, str(COLLECTORS))
import brief_feedback as f
import brief_workflow as w

NOW = "2026-10-21T12:00:00+08:00"
LATER = "2026-10-21T13:00:00+08:00"
PAGE = "https://app.notion.com/p/3ff15787f113817eacbbd30bb9bbed07"


def page(markdown):
    return {"url": PAGE, "complete": True, "markdown": markdown}


def message(**updates):
    result = {"role": "user", "quote": "这篇对我有用", "thread_id": "test-thread",
              "thread_url": "codex://threads/test-thread", "message_ref": "turn-1",
              "kind": "relevance", "scope": "item", "label": "interesting",
              "original_url": "https://example.org/release/1"}
    result.update(updates)
    return result


class InlineFeedbackTests(unittest.TestCase):
    def test_native_markdown_binds_multiple_items_on_same_report(self):
        snapshot = f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)\n"
                                          "- [ ] 👍 [Useful](https://example.org/b)"), NOW, "inline_one")
        self.assertEqual([True, False], [i["checked"] for i in snapshot["items"]])
        signals = w.feedback_signals(snapshot)["signals"]
        self.assertEqual(1, len(signals))
        self.assertEqual(["https://example.org/a"], signals[0]["subjects"])
        two = f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)\n"
                                     "- [x] 👍 [Useful](https://example.org/b)"), NOW, "inline_two")
        self.assertEqual(2, len(w.feedback_signals(two)["signals"]))
        for url in [PAGE+"?pvs=204", "https://www.notion.so/Oct-21-Wed-3ff15787f113817eacbbd30bb9bbed07",
                    "https://app.notion.com/p/3ff15787-f113-817e-acbb-d30bb9bbed07"]:
            equivalent = page("- [x] 👍 [Useful](https://example.org/a)")
            equivalent["url"] = url
            self.assertEqual(snapshot["items"][0]["feedback_id"],
                             f.inline_snapshot(equivalent, NOW, "same")["items"][0]["feedback_id"])

    def test_unchecking_retracts_previous_like_without_creating_dislike(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            f.record_snapshot(root, f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)"), NOW, "like"))
            f.record_snapshot(root, f.inline_snapshot(page("- [ ] 👍 [Useful](https://example.org/a)"), LATER, "unlike"))
            index = f.build_index(root)
            self.assertEqual(2, len(index["records"]))
            self.assertEqual([], w.feedback_signals(index)["signals"])
            self.assertFalse(index["items"][0]["checked"])

    def test_missing_control_is_not_silent_retraction(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            f.record_snapshot(root, f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)"), NOW, "like"))
            f.record_snapshot(root, f.inline_snapshot(page("No control remains in this read"), LATER, "missing"))
            index = f.build_index(root)
            self.assertTrue(index["items"][0]["checked"])
            self.assertEqual("missing_control", index["items"][0]["observation_status"])
            self.assertEqual(1, len(index["coverage_gaps"]))
            self.assertEqual([], w.feedback_signals(index)["signals"])
            self.assertEqual(index["coverage_gaps"], w.feedback_signals(index)["coverage_gaps"])
            f.record_snapshot(root, f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)"),
                                                      "2026-10-21T14:00:00+08:00", "returned"))
            recovered = f.build_index(root)
            self.assertEqual([], recovered["coverage_gaps"])
            self.assertEqual(1, len(w.feedback_signals(recovered)["signals"]))

    def test_incomplete_unknown_malformed_and_repeated_controls_record_gaps(self):
        for field, value in [("complete", False), ("truncated", True), ("unknown_block_count", 1)]:
            p = page("- [x] 👍 [Useful](https://example.org/a)")
            p[field] = value
            snapshot = f.inline_snapshot(p, NOW, "test")
            self.assertFalse(snapshot["coverage"]["page_complete"])
            self.assertEqual([], snapshot["items"])
            f.validate_snapshot(snapshot)
        for text in ["- [x] 👍 [Useful](not-a-url)",
                     "- [x] 👍 [Useful](https://example.org/a)\n- [ ] 👍 [Useful](https://example.org/a?utm_source=x)"]:
            snapshot = f.inline_snapshot(page(text), NOW, "test")
            self.assertFalse(snapshot["coverage"]["page_complete"])
            self.assertEqual([], snapshot["items"])

    def test_code_examples_are_not_human_likes(self):
        for opening, inner, closing in [("```markdown", "~~~", "```"), ("~~~markdown", "```", "~~~"),
                                        ("````markdown", "```", "````"), ("~~~~", "~~~", "~~~~~"),
                                        ("```markdown", "    ```", "```"),
                                        ("\t```markdown", "\t    ```", "\t```")]:
            s = f.inline_snapshot(page(opening+"\n"+inner+"\n- [x] 👍 [Useful](https://example.org/a)\n"+closing), NOW, "test")
            self.assertEqual([], s["items"])
        for text in ["    - [x] 👍 [Useful](https://example.org/a)", "\t    - [x] 👍 [Useful](https://example.org/a)"]:
            s = f.inline_snapshot(page(text), NOW, "code")
            self.assertEqual([], s["items"])
        native = f.inline_snapshot(page("\t- [x] 👍 [Useful](https://example.org/a)"), NOW, "native")
        self.assertEqual(1, len(native["items"]))

    def test_failed_inline_read_is_persisted_without_retracting_history(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            f.record_snapshot(root, f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)"), NOW, "good"))
            failed = page("")
            failed["truncated"] = True
            f.record_snapshot(root, f.inline_snapshot(failed, LATER, "failed"))
            index = f.build_index(root)
            self.assertTrue(index["items"][0]["checked"])
            self.assertEqual("unrefreshed", index["items"][0]["observation_status"])
            self.assertEqual("inline_read_incomplete", index["coverage_gaps"][0]["kind"])
            self.assertFalse(index["feedback_coverage_complete"])
            self.assertEqual([], w.feedback_signals(index)["signals"])
            f.record_snapshot(root, f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)"),
                                                      "2026-10-21T14:00:00+08:00", "recovered"))
            self.assertTrue(f.build_index(root)["feedback_coverage_complete"])


class ChatFeedbackTests(unittest.TestCase):
    def test_quote_and_exact_item_survive_capture(self):
        s = f.chat_snapshot(message(), NOW)
        signal = w.feedback_signals(s)["signals"][0]
        self.assertEqual("这篇对我有用", signal["quote"])
        self.assertEqual("turn-1", signal["provenance"]["message_ref"])
        self.assertEqual(["https://example.org/release/1"], signal["subjects"])
        self.assertEqual("prefer_exact", signal["disposition"])

    def test_presentation_workflow_correction_and_ambiguity_are_not_relevance(self):
        for kind in ("presentation", "workflow", "correction", "unresolved"):
            s = f.chat_snapshot(message(kind=kind, scope="report", label=None, quote="内容太浅了"), NOW)
            self.assertEqual([], w.feedback_signals(s)["signals"])
            self.assertFalse(s["review_notes"][0]["human_gold_eligible"])
        with self.assertRaises(ValueError):
            f.chat_snapshot(message(scope="topic"), NOW)

    def test_assistant_quotes_and_missing_provenance_cannot_supply_labels(self):
        for change in ({"role": "assistant"}, {"message_ref": ""}, {"quote": ""}, {"thread_id": ""}):
            with self.assertRaises(ValueError):
                f.chat_snapshot(message(**change), NOW)
        for field in ["quote", "thread_id", "message_ref", "thread_url"]:
            for blank in [" ", "\n"]:
                with self.assertRaises(ValueError):
                    f.chat_snapshot(message(**{field: blank}), NOW)
                snapshot = f.chat_snapshot(message(), NOW)
                item = snapshot["items"][0]
                provenance = item["provenance"]
                provenance[field] = blank
                item["quote"] = provenance["quote"]
                provenance["quote_sha256"] = hashlib.sha256(provenance["quote"].encode()).hexdigest()
                item["feedback_id"] = "chat:" + f.fingerprint(provenance["thread_id"], provenance["message_ref"])
                snapshot["run_id"] = item["feedback_id"].replace(":", "_")
                item["row_url"] = provenance["thread_url"]
                with self.assertRaises(ValueError):
                    f.validate_snapshot(snapshot)

    def test_same_message_is_idempotent_across_capture_dates_and_cannot_be_relabeled(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            first = f.record_snapshot(root, f.chat_snapshot(message(), NOW))
            again = f.record_snapshot(root, f.chat_snapshot(message(), "2026-10-22T12:00:00+08:00"))
            self.assertEqual(first, again)
            with self.assertRaises(ValueError):
                f.record_snapshot(root, f.chat_snapshot(message(label="not_interesting"), LATER))
            self.assertEqual(1, len(list((root / "brief-workflow/feedback").glob("*/*.json"))))
            changed_run = f.chat_snapshot(message(label="not_interesting"), LATER)
            changed_run["run_id"] = "another_name"
            with self.assertRaises(ValueError):
                f.record_snapshot(root, changed_run)
            # Simulate direct cloud creation bypassing the local writer.
            again = f.chat_snapshot(message(), "2026-10-22T12:00:00+08:00")
            duplicate = root / "brief-workflow/feedback/2026-10-22" / (again["run_id"]+".json")
            duplicate.parent.mkdir()
            duplicate.write_bytes(f.encoded(again))
            self.assertEqual(1, len(w.feedback_signals(f.build_index(root))["signals"]))
            forged = f.chat_snapshot(message(label="not_interesting"), "2026-10-22T12:00:00+08:00")
            duplicate.write_bytes(f.encoded(forged))
            with self.assertRaises(ValueError):
                f.build_index(root)

    def test_conflicting_sources_remain_visible_and_review_notes_are_retained(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            f.record_snapshot(root, f.chat_snapshot(message(label="not_interesting", quote="这篇我不感兴趣"), NOW))
            f.record_snapshot(root, f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/release/1)"), LATER, "inline"))
            f.record_snapshot(root, f.chat_snapshot(message(kind="presentation", scope="report", label=None,
                                                           quote="解释应再深入一点", message_ref="turn-2"), LATER))
            index = f.build_index(root)
            self.assertEqual(["https://example.org/release/1"], index["conflicting_subjects"])
            self.assertEqual(2, len(w.feedback_signals(index)["signals"]))
            self.assertEqual(1, len(w.feedback_signals(index)["review_notes"]))
            f.atomic_write(root / "brief-workflow/feedback/index.json", f.encoded(index))
            self.assertEqual(index, f.build_index(root))

    def test_original_reading_queue_snapshot_remains_compatible(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            value = {"schema_version": 1, "run_id": "queue", "observed_at": NOW,
                     "items": [{"row_url": "https://app.notion.com/p/row", "original_url": "https://example.org/a",
                                "tags": ["Interested"], "reading_status": "Seen", "next": "Keep my note"}]}
            f.record_snapshot(root, value)
            index = f.build_index(root)
            signal = w.feedback_signals(index)["signals"][0]
            self.assertEqual("prefer_exact", signal["disposition"])
            self.assertEqual("Keep my note", signal["next"])
            self.assertEqual(["Interested"], value["items"][0]["tags"])

    def test_queue_incomplete_and_unknown_coverage_remain_visible_until_recovery(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            value = {"schema_version": 1, "run_id": "queue", "observed_at": NOW,
                     "items": [{"row_url": "queue-row", "original_url": "https://example.org/a", "tags": ["Interesting"]}]}
            f.record_snapshot(root, value)
            self.assertEqual("queue_coverage_unknown", f.build_index(root)["coverage_gaps"][0]["kind"])
            failed = {"schema_version": 1, "run_id": "partial", "observed_at": LATER,
                      "coverage_complete": False, "items": []}
            f.record_snapshot(root, failed)
            index = f.build_index(root)
            self.assertEqual("queue_read_incomplete", index["coverage_gaps"][0]["kind"])
            self.assertEqual([], w.feedback_signals(index)["signals"])
            value.update(run_id="recovered", observed_at="2026-10-21T14:00:00+08:00", coverage_complete=True)
            f.record_snapshot(root, value)
            self.assertTrue(f.build_index(root)["feedback_coverage_complete"])
            self.assertEqual(1, len(w.feedback_signals(f.build_index(root))["signals"]))

    def test_manually_constructed_records_cannot_bypass_provenance_or_checkbox_state(self):
        chat = f.chat_snapshot(message(), NOW)
        chat["items"][0]["provenance"]["role"] = "assistant"
        with self.assertRaises(ValueError):
            f.validate_snapshot(chat)
        inline = f.inline_snapshot(page("- [ ] 👍 [Useful](https://example.org/a)"), NOW, "inline")
        inline["items"][0]["tags"] = ["Interesting"]
        with self.assertRaises(ValueError):
            f.validate_snapshot(inline)
        inline = f.inline_snapshot(page("- [x] 👍 [Useful](https://example.org/a)"), NOW, "inline")
        inline["items"][0]["observation_status"] = "missing_control"
        with self.assertRaises(ValueError):
            f.validate_snapshot(inline)
        for wrong_kind in ["reading_queue", "unknown"]:
            chat = f.chat_snapshot(message(), NOW)
            chat["items"][0]["source_kind"] = wrong_kind
            chat["items"][0].pop("provenance")
            with self.assertRaises(ValueError):
                f.validate_snapshot(chat)
        for field in ["thread_id", "message_ref", "thread_url", "quote_sha256"]:
            note = f.chat_snapshot(message(kind="presentation", label=None), NOW)
            note["review_notes"][0]["provenance"].pop(field)
            with self.assertRaises(ValueError):
                f.validate_snapshot(note)
        queue = {"schema_version": 1, "run_id": "queue", "observed_at": NOW,
                 "items": [{"row_url": "queue-row", "original_url": "https://example.org/a",
                            "tags": ["Interesting"], "feedback_id": f.chat_snapshot(message(), NOW)["items"][0]["feedback_id"]}]}
        with self.assertRaises(ValueError):
            f.validate_snapshot(queue)
        for snapshot in [f.inline_snapshot(page("- [ ] 👍 [Useful](https://example.org/a)"), NOW, "inline"),
                         f.chat_snapshot(message(), NOW),
                         f.chat_snapshot(message(kind="presentation", label=None), NOW)]:
            snapshot["observed_at"] = LATER
            with self.assertRaises(ValueError):
                f.validate_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
