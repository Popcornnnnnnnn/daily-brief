"""Synthetic audit fixtures only; no network, collection or delivery calls."""
import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collectors"))
import brief_workflow as w


def failure_fixture():
    record = {"schema_version": 1, "run_id": "synthetic_dispatch_failure",
              "mode": "shadow", "report_date": "2026-10-31",
              "window_start": "2026-10-30T04:00:00+08:00",
              "window_end": "2026-10-31T04:00:00+08:00",
              "input_revision": "a" * 40, "policy_version": "brief-policy-v1",
              "strategy_version": "editorial-v1", "candidate_count": 0,
              "unreviewed_count": 0, "decisions": [],
              "editorial_complete": False, "delivery_verified": False}
    stages = [{"stage_id": sid, "status": "not_attempted", "started_at": None,
               "ended_at": None, "evidence": [], "error": None,
               "cause_proven": False, "next_step": None} for sid in sorted(w.AUDIT_STAGES)]
    by_id = {s["stage_id"]: s for s in stages}
    by_id["trigger"].update(status="success", evidence=["synthetic trigger fixture"])
    by_id["collector_dispatch"].update(status="failed",
        error={"code": "dispatch_rejected", "message": "Synthetic dispatch rejection"},
        evidence=["synthetic tool response: dispatch rejected"], cause_proven=True,
        next_step="Inspect dispatch authorization; do not infer login state.")
    record["execution_audit"] = {"schema_version": 1, "run_id": record["run_id"],
        "collector_thread_id": w.COLLECTOR_THREAD_ID, "stages": stages,
        "counts": dict.fromkeys(sorted(w.AUDIT_COUNTS)),
        "user_received": "unknown", "user_received_evidence": []}
    return record


class ExecutionAuditTests(unittest.TestCase):
    def test_synthetic_dispatch_failure_passes_and_remains_unknown(self):
        r = failure_fixture()
        self.assertIs(w.validate_record(r), r)
        self.assertIsNone(r["execution_audit"]["counts"]["remaining"])
        self.assertEqual("unknown", r["execution_audit"]["user_received"])

    def test_legacy_record_unchanged(self):
        r = failure_fixture()
        del r["execution_audit"]
        self.assertIs(w.validate_record(r), r)

    def test_rejects_invalid_evidence_and_status_claims(self):
        def stage(r, sid):
            return next(s for s in r["execution_audit"]["stages"] if s["stage_id"] == sid)
        mutations = [
            lambda r: r["execution_audit"].update(run_id="another-run"),
            lambda r: r["execution_audit"].update(collector_thread_id="another-collector"),
            lambda r: r["execution_audit"]["stages"].pop(),
            lambda r: r["execution_audit"]["stages"].append(copy.deepcopy(r["execution_audit"]["stages"][0])),
            lambda r: stage(r, "trigger").update(evidence=[]),
            lambda r: stage(r, "trigger").update(status="completed"),
            lambda r: stage(r, "collector_dispatch").update(error=None),
            lambda r: stage(r, "collector_dispatch").update(next_step=None),
            lambda r: stage(r, "collector_dispatch").update(error={"code":"login_expired","message":"Login expired"},cause_proven=False),
            lambda r: stage(r, "collector_dispatch").update(started_at="2026-10-31T04:00:00"),
            lambda r: stage(r, "collector_dispatch").update(started_at="2026-10-31T04:01:00+08:00",ended_at="2026-10-31T04:00:00+08:00"),
            lambda r: stage(r, "source_access").update(started_at="2026-10-31T04:00:00+08:00"),
            lambda r: r["execution_audit"]["counts"].update(remaining=-1),
            lambda r: r["execution_audit"]["counts"].update(remaining=True),
            lambda r: r["execution_audit"]["counts"].update(fully_read=1),
            lambda r: r["execution_audit"].update(user_received="confirmed"),
            lambda r: stage(r, "trigger").update(evidence=["access_token=secret"]),
        ]
        for n, mutate in enumerate(mutations):
            with self.subTest(case=n):
                r=failure_fixture(); mutate(r)
                with self.assertRaises(ValueError): w.validate_record(r)

    def test_per_platform_partial_results_are_distinct(self):
        r=failure_fixture()
        s={"stage_id":"source_access.douyin", "status":"unknown", "started_at":None,
           "ended_at":None, "evidence":[], "error":None, "cause_proven":False,
           "next_step":"Read the actual source-access receipt."}
        r["execution_audit"]["stages"].append(s)
        self.assertIs(w.validate_record(r),r)


if __name__ == "__main__":
    unittest.main()
