import copy
import datetime as dt
import importlib.util
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collectors"))
import brief_workflow as w


def item(n, family="security", source="advisories", run="run-a"):
    return {"source_id": source, "source_name": source, "family": family,
            "source_item_id": str(n), "title": f"Item {n}",
            "url": f"https://example.org/{source}/{n}",
            "canonical_url": f"https://example.org/{source}/{n}",
            "published_at": "2026-10-20T12:00:00Z", "published_precision": "datetime",
            "first_seen_at": "2026-10-20T12:01:00Z", "last_seen_at": "2026-10-20T12:01:00Z",
            "summary": "Original source material", "author": "", "raw_run_id": run}


def payload(items, coverage=None):
    return {"schema_version": 2, "run_id": "run-a", "generated_at": "2026-10-20T13:00:00Z",
            "collection_lookback_hours": 96, "export_hours": 48,
            "coverage": coverage or [{"source_id": "advisories", "status": "complete", "method": "api"}],
            "items": items}


def record(decisions=None, count=2):
    return {"schema_version": 1, "run_id": "editorial-a", "report_date": "2026-10-21",
            "window_start": "2026-10-20T04:00:00+08:00", "window_end": "2026-10-21T04:00:00+08:00",
            "policy_version": w.POLICY_VERSION, "strategy_version": "editorial-v1", "input_revision": "a" * 40,
            "candidate_count": count, "decisions": decisions or [],
            "unreviewed_count": count - sum(x["disposition"] != "unreviewed" for x in decisions or []),
            "editorial_complete": False, "delivery_verified": False}


def selected(cid="one"):
    return {"candidate_id": cid, "event_id": "release:example/v1", "disposition": "selected",
            "reason": "A first-party release changes the documented API",
            "evidence_urls": ["https://example.org/releases/v1"]}


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, kind, data):
        return w.write_input_manifest(self.root, kind, data, {}, Path(w.__file__))

    def test_high_volume_family_does_not_remove_low_volume_family(self):
        items = [item(n) for n in range(3100)] + [item("paper", "research", "papers")]
        m = self.write("tech-ai", payload(items))
        read, restored = w.read_inputs(self.root, "tech-ai")
        self.assertEqual(3101, read["candidate_count"])
        self.assertEqual(1, read["family_counts"]["research"])
        self.assertEqual({i["candidate_id"] for i in items}, {i["candidate_id"] for i in restored})
        self.assertTrue(all(p["count"] <= 25 for p in m["partitions"]))

    def test_bound_includes_wrapper_and_indentation(self):
        values = [dict(item(n), summary="s" * 400) for n in range(50)]
        manifest = self.write("tech-ai", payload(values))
        self.assertTrue(all(p["bytes"] <= w.PART_BYTES for p in manifest["partitions"]))
        self.assertEqual(50, len(w.read_inputs(self.root, "tech-ai")[1]))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.write("tech-ai", payload([dict(item(1), summary="s" * w.PART_BYTES)]))

    def test_wrong_hash_and_mixed_run_fail_closed(self):
        m = self.write("tech-ai", payload([item(1)]))
        path = self.root / m["partitions"][0]["path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "hash"):
            w.read_inputs(self.root, "tech-ai")
        self.write("tech-ai", payload([item(1)]))
        data = json.loads(path.read_bytes())
        data["run_id"] = "other-run"
        body = w.encoded(data)
        path.write_bytes(body)
        m["partitions"][0].update(sha256=w.digest(body), bytes=len(body))
        (path.parent / "manifest.json").write_bytes(w.encoded(m))
        with self.assertRaisesRegex(ValueError, "Mixed"):
            w.read_inputs(self.root, "tech-ai")

    def test_cached_candidates_do_not_hide_failed_current_source(self):
        m = self.write("tech-ai", payload([item(1, run="older-run")],
                    [{"source_id": "advisories", "status": "failed", "method": "api"}]))
        self.assertFalse(m["source_coverage_complete"])
        self.assertFalse(w.read_inputs(self.root, "tech-ai")[1][0]["fresh_in_run"])

    def test_page_cleanup_preserves_other_collector_and_receipts(self):
        self.write("tech-ai", payload([item(n) for n in range(80)]))
        self.write("open-source", payload([item(1)]))
        receipt = self.root / "brief-workflow/records/keep.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("keep")
        self.write("tech-ai", payload([item(1)]))
        self.assertEqual(1, len(list((self.root / "brief-workflow/inputs/tech-ai").glob("*-????.json"))))
        self.assertTrue((self.root / "brief-workflow/inputs/open-source/manifest.json").exists())
        self.assertEqual("keep", receipt.read_text())

    def test_fixed_window_and_original_date_boundary(self):
        sample = item(1)
        self.assertEqual("news_window", w.time_class(sample, "2026-10-21"))
        sample["published_at"] = "2026-10-20T20:00:00Z"
        self.assertEqual("after_cutoff", w.time_class(sample, "2026-10-21"))
        sample["published_precision"] = "first_seen_only"
        self.assertEqual("needs_original_date", w.time_class(sample, "2026-10-21"))
        sample.update(published_precision="datetime", discovery_method="sitemap")
        self.assertEqual("needs_original_date", w.time_class(sample, "2026-10-21"))

    def test_prepare_records_exact_inputs_without_claiming_delivery(self):
        self.write("tech-ai", payload([item(1)]))
        self.write("open-source", payload([item(2)]))
        result = w.prepare(self.root, "b" * 40, "2026-10-21", self.root / "shadow")
        self.assertEqual(2, result["candidate_count"])
        self.assertFalse(result["editorial_complete"])
        self.assertFalse(result["delivery_verified"])
        self.assertEqual(2, len((self.root / "shadow/candidate-register.jsonl").read_text().splitlines()))


class EditorialTests(unittest.TestCase):
    def test_feedback_is_explicit_and_exact(self):
        value = {"schema_version": 1, "observed_at": "2026-10-21T10:00:00+08:00", "items": [
            {"row_url": "row/1", "original_url": "https://example.org/a", "tags": [], "reading_status": "Seen"},
            {"row_url": "row/2", "original_url": "https://example.org/b", "tags": ["Not Interested"], "reading_status": "Unread"},
            {"row_url": "row/3", "original_url": "https://example.org/c", "tags": ["Try", "Interested"]},
            {"row_url": "row/4", "original_url": "https://example.org/d", "tags": ["Later"]}]}
        result = w.feedback_signals(value)
        self.assertEqual(3, len(result["signals"]))
        self.assertEqual(["avoid_exact", "prefer_exact", "park_exact"], [s["disposition"] for s in result["signals"]])
        self.assertEqual(["https://example.org/b"], result["signals"][0]["subjects"])
        self.assertEqual(["Interested", "Try"], sorted(value["items"][2]["tags"]))

    def test_record_idempotence_and_event_history(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            value = record([selected()])
            p = w.record_receipt(root, value, {"one", "two"})
            self.assertEqual(p, w.record_receipt(root, copy.deepcopy(value), {"one", "two"}))
            changed = copy.deepcopy(value)
            changed["decisions"][0]["reason"] = "Different claim"
            with self.assertRaisesRegex(ValueError, "immutable"):
                w.record_receipt(root, changed)
            changed["run_id"] = "editorial-correction"
            changed["correction_of"] = "editorial-a"
            w.record_receipt(root, changed)
            result = w.history(root)
            self.assertEqual(2, len(result["events"]["release:example/v1"]))
            self.assertEqual(2, len(result["receipts"]))

    def test_shadow_and_pending_decisions_are_not_proof_of_delivery(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            value = record([selected()])
            value["mode"] = "shadow"
            w.record_receipt(root, value)
            self.assertEqual({}, w.history(root)["events"])
            value["run_id"], value["mode"] = "production-run", "production"
            w.record_receipt(root, value)
            self.assertFalse(w.history(root)["events"]["release:example/v1"][0]["delivery_verified"])

    def test_unknown_identity_and_false_complete_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            w.validate_record(record([selected()]), {"other"})
        value = record([selected()])
        value["editorial_complete"] = True
        with self.assertRaisesRegex(ValueError, "Unread"):
            w.validate_record(value)
        value = record([selected(), selected()])
        with self.assertRaisesRegex(ValueError, "repeated"):
            w.validate_record(value)

    def test_evaluation_counts_failures_and_does_not_promote_fixtures(self):
        baseline = record([])
        candidate = record([selected()])
        gold = {"cases": [{"candidate_id": "one", "expected": "select", "origin": "policy_fixture", "split": "holdout"}]}
        result = w.evaluate(gold, baseline, candidate)
        self.assertEqual(0, result["scores"]["baseline"]["coverage"])
        self.assertEqual(1, result["scores"]["candidate"]["complete_accuracy"])
        self.assertFalse(result["promotion_eligible"])
        candidate["input_revision"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "same immutable"):
            w.evaluate(gold, baseline, candidate)


class BridgeConcurrencyTests(unittest.TestCase):
    def test_cloud_receipt_survives_a_push_race(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            def git(*args, cwd=None):
                return subprocess.run(["git", *map(str, args)], cwd=cwd, check=True,
                                      capture_output=True, text=True).stdout.strip()
            remote, local, cloud = root / "remote.git", root / "local", root / "cloud"
            git("init", "--bare", "--initial-branch=main", remote)
            git("clone", remote, local)
            for folder in [local]:
                git("config", "user.name", "Fixture", cwd=folder)
                git("config", "user.email", "fixture@example.invalid", cwd=folder)
            (local / "seed").write_text("seed")
            git("add", "seed", cwd=local)
            git("commit", "-m", "seed", cwd=local)
            git("push", "origin", "main", cwd=local)
            git("clone", remote, cloud)
            git("config", "user.name", "Fixture", cwd=cloud)
            git("config", "user.email", "fixture@example.invalid", cwd=cloud)
            (local / "collector-output").write_text("collector")
            git("add", "collector-output", cwd=local)
            git("commit", "-m", "collector", cwd=local)
            (cloud / "cloud-receipt").write_text("human disposition unchanged")
            w.record_receipt(cloud, record([selected()]))
            git("add", "cloud-receipt", cwd=cloud)
            git("add", "brief-workflow/records", cwd=cloud)
            git("commit", "-m", "cloud", cwd=cloud)
            git("push", "origin", "main", cwd=cloud)
            w.push_bridge(local)
            git("pull", "--ff-only", cwd=cloud)
            self.assertEqual("collector", (cloud / "collector-output").read_text())
            self.assertEqual("human disposition unchanged", (local / "cloud-receipt").read_text())
            index = json.loads((local / "brief-workflow/history-latest.json").read_bytes())
            self.assertEqual("editorial-a", index["receipts"][0]["run_id"])
            (local / "unrelated").write_text("preserve staging")
            git("add", "unrelated", cwd=local)
            with self.assertRaisesRegex(ValueError, "staged"):
                w.sync_bridge(local)
            self.assertIn("unrelated", git("diff", "--cached", "--name-only", cwd=local))


class CollectorRegressionTests(unittest.TestCase):
    def load_collector(self, kind):
        path = Path(__file__).resolve().parents[1] / "collectors" / kind / "collector.py"
        spec = importlib.util.spec_from_file_location("pagination_" + kind.replace("-", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_http_error_cannot_become_an_empty_success_via_curl(self):
        mod = self.load_collector("open-source")
        error = mod.urllib.error.HTTPError("https://example.org", 429, "rate limited", {}, None)
        def curl(args, **kwargs):
            if "--fail" in args:
                raise subprocess.CalledProcessError(22, args, stderr=b'HTTP 429')
            return subprocess.CompletedProcess(args, 0, stdout=b'<html>rate limited</html>')
        with patch.object(mod.urllib.request,"urlopen",side_effect=error), patch.object(mod.time,"sleep"), patch.object(mod.subprocess,"run",side_effect=curl):
            with self.assertRaises(subprocess.CalledProcessError):
                mod.http_get("https://example.org", 1)

    def test_oai_resumption_token_is_partial(self):
        mod = self.load_collector("tech-ai")
        xml = b'<OAI-PMH><ListRecords><resumptionToken>next-page</resumptionToken></ListRecords></OAI-PMH>'
        with tempfile.TemporaryDirectory() as t, patch.object(mod, "http_get", return_value=(xml, "xml", "https://example.org/oai")):
            mod.RAW_DIR = Path(t)
            _, status, _, detail = mod.fetch_source({"id":"arxiv", "type":"arxiv_oai", "url":"https://example.org/oai"},
                {"request_timeout_seconds":1,"max_items_per_source":2000}, "run-a")
            self.assertEqual("partial", status)
            self.assertIn("resumption token", detail)

    def test_oai_modification_date_never_replaces_paper_creation(self):
        mod = self.load_collector("tech-ai")
        template = '<OAI-PMH><ListRecords><record><header><datestamp>2026-10-20T12:00:00Z</datestamp></header><metadata><arXivRaw><id>2001.00001</id><title>Older paper</title><categories>cs.AI</categories>{created}<abstract>Actual source</abstract></arXivRaw></metadata></record></ListRecords></OAI-PMH>'
        value = mod.parse_arxiv_oai(template.format(created='<created>2020-01-01</created>').encode())[0]
        self.assertEqual('2020-01-01T00:00:00Z', value['published_at'])
        self.assertEqual('date', value['published_precision'])
        missing = mod.parse_arxiv_oai(template.format(created='').encode())[0]
        self.assertIsNone(missing['published_at'])
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            mod.STATE_DIR,mod.RAW_DIR,mod.DB_PATH=root/'state',root/'raw',root/'state/db.sqlite'
            conn=mod.db_connect()
            legacy=dict(missing,published_at='2026-10-20T12:00:00Z',published_precision='datetime')
            mod.upsert_items(conn,'arxiv',[legacy],'legacy-run','2026-10-20T12:01:00Z')
            mod.upsert_items(conn,'arxiv',[missing],'fixed-run','2026-10-21T01:00:00Z')
            row=conn.execute('SELECT published_at,published_precision FROM items').fetchone()
            self.assertIsNone(row['published_at'])
            self.assertEqual('unknown',row['published_precision'])
            conn.close()

    def test_sitemap_child_ceiling_is_partial(self):
        mod = self.load_collector("tech-ai")
        root = ('<sitemapindex>' + ''.join(f'<sitemap><loc>https://example.org/{n}</loc></sitemap>' for n in range(11)) + '</sitemapindex>').encode()
        replies = [(root, "xml", "https://example.org/map")] + [(b'<urlset/>', "xml", "https://example.org/child")] * 10
        with tempfile.TemporaryDirectory() as t, patch.object(mod,"http_get",side_effect=replies):
            mod.RAW_DIR = Path(t)
            _, status, _, detail = mod.fetch_source({"id":"map", "type":"sitemap_index", "url":"https://example.org/map"},
                {"request_timeout_seconds":1,"max_items_per_source":2000}, "run-a")
            self.assertEqual("partial",status)
            self.assertIn("11 children",detail)
            self.assertEqual(10,len(list(Path(t).glob('run-a-map-sitemap-*.gz'))))
            import gzip
            self.assertEqual(b'<urlset/>',gzip.decompress((Path(t)/'run-a-map-sitemap-0001.gz').read_bytes()))

    def test_watched_commit_ceiling_preserves_items_and_marks_partial(self):
        mod = self.load_collector("open-source")
        def response(endpoint, fields=None):
            if endpoint.endswith('/releases?per_page=100'):return b'[]'
            if endpoint.endswith('/commits'):
                return json.dumps([{"sha":f"{fields['page']}-{n}","html_url":f"https://example.org/{fields['page']}/{n}",
                    "commit":{"message":"change","committer":{"date":"2026-10-20T12:00:00Z"}}} for n in range(100)]).encode()
            return b'{"full_name":"owner/repo"}'
        with tempfile.TemporaryDirectory() as t, patch.object(mod,"gh_api",side_effect=response):
            mod.RAW_DIR = Path(t)
            items, status, _, detail = mod.fetch_source({"id":"watch","type":"watched_repo","repo":"owner/repo"},
                {"request_timeout_seconds":1,"lookback_hours":96},"run-a","2026-10-20T12:00:00Z")
            self.assertEqual(1000,len(items))
            self.assertEqual("partial",status)
            self.assertIn("pagination ceiling",detail)
    def test_export_preserves_rare_family_beyond_previous_global_cap(self):
        for kind, old_cap in [("tech-ai", 2000), ("open-source", 3000)]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as t:
                spec = importlib.util.spec_from_file_location("collector_" + kind.replace("-", "_"),
                    Path(__file__).resolve().parents[1] / "collectors" / kind / "collector.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                root = Path(t)
                mod.STATE_DIR, mod.RAW_DIR, mod.DB_PATH = root / "state", root / "raw", root / "state/db.sqlite"
                mod.EXPORT_DIR = root / "exports"
                mod.LATEST_MD = mod.EXPORT_DIR / "Latest.md"
                mod.LATEST_JSON = mod.EXPORT_DIR / "Latest.json"
                fixed = dt.datetime(2026, 10, 20, 13, tzinfo=dt.timezone.utc)
                with patch.object(mod, "now_utc", return_value=fixed):
                    conn = mod.db_connect()
                    for source, family in [("advisories", "security"), ("papers", "research")]:
                        conn.execute("INSERT INTO sources(source_id,name,family,source_type) VALUES(?,?,?,?)", (source, source, family, "feed"))
                    many = [item(n) for n in range(old_cap + 5)]
                    rare = item("paper", "research", "papers")
                    rare["published_at"] = "2026-10-20T11:00:00Z"
                    mod.upsert_items(conn, "advisories", many, "run-a", "2026-10-20T12:01:00Z")
                    mod.upsert_items(conn, "papers", [rare], "run-a", "2026-10-20T12:01:00Z")
                    conn.commit()
                    mod.export_bundle(conn, "run-a", {"lookback_hours": 96, "export_hours": 48})
                    exported = json.loads(mod.LATEST_JSON.read_bytes())
                    self.assertEqual(old_cap + 6, len(exported["items"]))
                    self.assertTrue(any(x["family"] == "research" for x in exported["items"]))
                    manifest, restored = w.read_inputs(mod.EXPORT_DIR, kind)
                    self.assertEqual(len(exported["items"]), manifest["candidate_count"])
                    self.assertEqual(len(exported["items"]), len(restored))
                    conn.close()

    def test_source_retention_limit_is_partial_and_raw_evidence_survives(self):
        path = Path(__file__).resolve().parents[1] / "collectors/tech-ai/collector.py"
        spec = importlib.util.spec_from_file_location("tech_limit", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        feed = b'<rss><channel>' + b''.join(f'<item><title>{n}</title><link>https://example.org/{n}</link></item>'.encode() for n in range(3)) + b'</channel></rss>'
        with tempfile.TemporaryDirectory() as t, patch.object(mod, "http_get", return_value=(feed, "xml", "https://example.org/feed")):
            mod.RAW_DIR = Path(t)
            items, status, _, detail = mod.fetch_source({"id": "sample", "type": "feed", "url": "https://example.org/feed"},
                {"request_timeout_seconds": 1, "max_items_per_source": 2}, "run-a")
            self.assertEqual(2, len(items))
            self.assertEqual("partial", status)
            self.assertIn("Parsed 3", detail)
            self.assertTrue((Path(t) / "run-a-sample.gz").exists())


if __name__ == "__main__":
    unittest.main()
