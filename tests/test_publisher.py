import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "publisher"))
import export_notion as exp
import privacy_gate as gate


def rt(text: str, *, bold: bool = False, href: str | None = None) -> dict:
    return {
        "type": "text",
        "plain_text": text,
        "href": href,
        "annotations": {"bold": bold, "italic": False, "code": False, "strikethrough": False},
        "text": {"content": text, "link": {"url": href} if href else None},
    }


def block(btype: str, texts: list[dict], *, children: list[dict] | None = None, extra: dict | None = None) -> dict:
    payload: dict = {"rich_text": texts}
    if extra:
        payload.update(extra)
    return {
        "id": f"blk-{btype}",
        "type": btype,
        "has_children": bool(children),
        "_children": children or [],
        btype: payload,
    }


class PublisherTests(unittest.TestCase):
    def test_strips_favorites_section_and_legacy_useful_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp) / "media"
            page = {
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "properties": {
                    "Date": {"date": {"start": "2026-11-22"}},
                    "Topic": {"title": [rt("Test Public Edition")]},
                    "Appendix": {"checkbox": False},
                },
            }
            blocks = [
                block("paragraph", [rt("Window: November 21, 2026 04:00 to November 22, 2026 04:00, Asia/Shanghai.")]),
                block("heading_2", [rt("Today in 90 seconds")]),
                block("bulleted_list_item", [rt("First clean item.", bold=True), rt(" Details here.")]),
                block("heading_2", [rt("Tech & AI")]),
                block("heading_3", [rt("Model update")]),
                block("paragraph", [rt("Released with "), rt("official notes", href="https://example.org/release"), rt(".")]),
                block("to_do", [rt("👍 "), rt("Useful", href="https://example.org/release")], extra={"checked": True}),
                block("heading_2", [rt("收藏与" + "录音")]),
                block("paragraph", [rt("Private " + "Xiaohongshu" + " ID 630ef4f3" + "000000000f005084")]),
                block("bulleted_list_item", [rt("Apple " + "Voice Memos secret note")]),
            ]
            meta, md = exp.convert_notion_page(
                page=page,
                blocks=blocks,
                page_date_map={"aaaaaaaa111122223333444444444444": "2026-11-21"},
                media_dir=media_dir,
                issue_number=50,
            )
            self.assertEqual("2026-11-22", meta["date"])
            self.assertEqual(["Today in 90 seconds", "Tech & AI"], meta["sections"])
            self.assertIn("First clean item.", md)
            self.assertNotIn("收藏与" + "录音", md)
            self.assertNotIn("630ef4f3" + "000000000f005084", md)
            self.assertNotIn("Useful", md)

    def test_rewrites_edition_notion_links_and_strips_unknown_notion_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp) / "media"
            known_id = "11111111222233334444555555555555"
            unknown_id = "99999999888877776666555555555555"
            notion_host = "https://app." + "notion.com/p/"
            page = {
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "properties": {
                    "Date": {"date": {"start": "2026-11-23"}},
                    "Topic": {"title": [rt("Link Rewrite Test")]},
                },
            }
            blocks = [
                block("heading_1", [rt("Tech & AI")]),
                block("paragraph", [rt("Follow-up from "), rt("yesterday", href=f"{notion_host}{known_id}"), rt(" and "), rt("private note", href=f"{notion_host}{unknown_id}"), rt(".")]),
            ]
            _, md = exp.convert_notion_page(
                page=page,
                blocks=blocks,
                page_date_map={known_id: "2026-11-22"},
                media_dir=media_dir,
            )
            self.assertIn("[yesterday](/issues/2026-11-22/)", md)
            self.assertIn("private note", md)
            self.assertNotIn("notion.com", md)

    def test_localizes_notion_file_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp) / "media"
            png_bytes = b"\x89PNG\r\n\x1a\nfake-image-payload"
            page = {
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "properties": {
                    "Date": {"date": {"start": "2026-11-23"}},
                    "Topic": {"title": [rt("Image Localization")]},
                },
            }
            blocks = [
                block("heading_1", [rt("Open Source")]),
                {
                    "id": "img1",
                    "type": "image",
                    "has_children": False,
                    "_children": [],
                    "image": {
                        "type": "file",
                        "file": {"url": "https://prod-files-secure.s3.us-west-2.amazonaws.com/expiring.png"},
                        "caption": [rt("Architecture diagram")],
                    },
                },
            ]
            meta, md = exp.convert_notion_page(
                page=page,
                blocks=blocks,
                page_date_map={},
                media_dir=media_dir,
                media_fetcher=lambda _u: (png_bytes, "image/png"),
            )
            self.assertIsNotNone(meta["lead_image"])
            src = meta["lead_image"]["src"]
            self.assertTrue(src.startswith("/media/2026-11-23/"))
            self.assertIn(src, md)
            saved_file = media_dir / src.removeprefix("/media/")
            self.assertEqual(png_bytes, saved_file.read_bytes())

    def test_privacy_gate_catches_leaks_and_passes_clean_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "editions").mkdir()
            clean = root / "editions" / "2026-11-23.md"
            clean.write_text("---\ntitle: \"Clean Edition\"\n---\n\n## Tech & AI\n\nAll public.\n", encoding="utf-8")
            self.assertEqual([], gate.scan_repository(root, extra_terms_file=root / "none.txt"))

            leaked = root / "editions" / "2026-11-22.md"
            leaked.write_text("## " + "收藏与" + "录音\n\nhttps://app." + "notion.com/p/123\n", encoding="utf-8")
            violations = gate.scan_repository(root, extra_terms_file=root / "none.txt")
            self.assertGreaterEqual(len(violations), 2)

    def test_strips_october_historical_feedback_shanghai_saved_and_unwraps_english(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp) / "media"
            page = {
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "properties": {
                    "Date": {"date": {"start": "2026-10-22"}},
                    "Topic": {"title": [rt("October Cleanup Test")]},
                },
            }
            blocks = [
                block("quote", [rt("Window: 2026-10-21 04:00–2026-10-22 04:00 (Asia/Shanghai)")]),
                block("heading_1", [rt("English")]),
                block("paragraph", [rt("Historical feedback (preserved during migration; not a native historical comment)")]),
                block("paragraph", [rt("Original author: Private Author")]),
                block("paragraph", [rt("Some migrated quote")]),
                block("paragraph", [rt("This preserves historical feedback only; it is not a new comment.")]),
                block("heading_2", [rt("30-second summary")]),
                block("numbered_list_item", [rt("First clean item.")]),
                block("heading_2", [rt("Shanghai events")]),
                block("bulleted_list_item", [rt("Local event")]),
                block("heading_2", [rt("Saved items & recordings")]),
                block("paragraph", [rt("Private recording note")]),
                block("heading_2", [rt("Favorites and recordings")]),
                block("paragraph", [rt("Private favorite note")]),
                block("heading_2", [rt("Tech & AI")]),
                block("heading_3", [rt("Real public story")]),
                block("paragraph", [rt("Public content.")]),
            ]
            meta, md = exp.convert_notion_page(
                page=page,
                blocks=blocks,
                page_date_map={},
                media_dir=media_dir,
                issue_number=19,
            )
            self.assertEqual("2026-10-21 04:00–2026-10-22 04:00 (Asia/Shanghai)", meta["window"])
            self.assertEqual(["First clean item."], meta["takeaways"])
            self.assertEqual(["30-second summary", "Tech & AI"], meta["sections"])
            self.assertIn("### Real public story", md)
            self.assertNotIn("Historical feedback", md)
            self.assertNotIn("Some migrated quote", md)
            self.assertNotIn("Shanghai events", md)
            self.assertNotIn("Saved items", md)
            self.assertNotIn("Favorites and recordings", md)

    def test_extracts_callout_90s_brief_and_drops_title_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            media_dir = Path(tmp) / "media"
            page = {
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "properties": {
                    "Date": {"date": {"start": "2026-11-21"}},
                    "Topic": {"title": [rt("Callout Brief Edition")]},
                },
            }
            blocks = [
                block("paragraph", [rt("Daily AI & Developer Brief — November 21, 2026", bold=True)]),
                block(
                    "callout",
                    [rt("The 90-second brief", bold=True)],
                    children=[
                        block("bulleted_list_item", [rt("First callout takeaway.")]),
                        block("bulleted_list_item", [rt("Second callout takeaway.")]),
                    ],
                ),
                block("heading_2", [rt("Tech & AI")]),
                block("heading_3", [rt("AlphaProof formalizes Signal")]),
                block("paragraph", [rt("Details.")]),
            ]
            meta, md = exp.convert_notion_page(
                page=page,
                blocks=blocks,
                page_date_map={},
                media_dir=media_dir,
            )
            self.assertEqual(["First callout takeaway.", "Second callout takeaway."], meta["takeaways"])
            self.assertIn("## Today in 90 seconds", md)
            self.assertNotIn("Daily AI & Developer Brief — November 21, 2026", md)
            self.assertIn("Math & Research", meta["tags"])


if __name__ == "__main__":
    unittest.main()
