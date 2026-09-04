from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SLUG = "aginti-autonomous-lab-ai-glasses-2b85b0d9"
TRANSCRIPT_PATH = ROOT / "data" / "transcripts" / f"{SLUG}.json"
PROOF_ROOT = ROOT / "data" / "proofs" / SLUG
SUBTITLE_ROOT = ROOT / "media" / "subtitles" / SLUG
PAGE_PATH = ROOT / "videos" / f"{SLUG}.html"

SPEC = importlib.util.spec_from_file_location("collect_lala_medias", ROOT / "scripts" / "collect_lala_medias.py")
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


class MultilingualSearchProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.transcript = json.loads(TRANSCRIPT_PATH.read_text(encoding="utf-8"))
        cls.concepts = json.loads((PROOF_ROOT / "concepts.json").read_text(encoding="utf-8"))
        cls.manifest = json.loads((PROOF_ROOT / "source-manifest.json").read_text(encoding="utf-8"))

    def test_all_45_search_strings_are_present(self) -> None:
        entries = self.transcript["entries"]
        self.assertEqual(15, len(entries))
        strings = [entry["tracks"][lang]["text"] for entry in entries for lang in ("ja", "en", "zh")]
        self.assertEqual(45, len(strings))
        self.assertTrue(all(strings))

    def test_japanese_ruby_is_normalized_and_idempotent(self) -> None:
        normalized = copy.deepcopy(self.transcript)
        collector.normalize_transcript_tracks(normalized)
        self.assertEqual(self.transcript, normalized)
        for entry in self.transcript["entries"]:
            track = entry["tracks"]["ja"]
            self.assertNotRegex(track["text"], r"\[[^]]+\]|<[^>]+>")
            self.assertNotIn("<ruby><ruby>", track["html"])
            self.assertEqual(track["html"].count("<ruby>"), track["html"].count("</ruby>"))

    def test_vtt_exports_are_exactly_reproducible(self) -> None:
        for lang in ("en", "ja"):
            expected = collector.transcript_to_vtt(self.transcript, lang)
            actual = (SUBTITLE_ROOT / f"transcript.{lang}.vtt").read_text(encoding="utf-8")
            self.assertEqual(expected, actual)
            self.assertEqual(15, actual.count(" --> "))

    def test_five_reviewed_concepts_have_timestamp_evidence(self) -> None:
        concepts = self.concepts["concepts"]
        self.assertEqual("reviewed-against-transcript", self.concepts["review_status"])
        self.assertEqual(
            {"concept:organoid", "concept:tumor-microenvironment", "concept:cancer-cell", "concept:hypothesis", "concept:evidence"},
            {concept["id"] for concept in concepts},
        )
        entries = self.transcript["entries"]
        edge_pairs = {
            (edge["source"], int(edge["target"].removeprefix("segment:")))
            for edge in self.concepts["edges"]
        }
        for concept in concepts:
            self.assertLess(concept["start_seconds"], concept["end_seconds"])
            self.assertTrue(concept["evidence_entries"])
            for index in concept["evidence_entries"]:
                self.assertIn((concept["id"], index), edge_pairs)
                self.assertGreaterEqual(entries[index]["start_seconds"], concept["start_seconds"])
                self.assertLessEqual(entries[index]["end_seconds"], concept["end_seconds"])

    def test_manifest_matches_canonical_media_and_limits_claims(self) -> None:
        media_path = ROOT / self.manifest["media"]["archive_path"]
        digest = hashlib.sha256(media_path.read_bytes()).hexdigest()
        self.assertEqual(self.manifest["media"]["sha256"], digest)
        self.assertFalse(self.manifest["claims"]["customer_result"])
        self.assertFalse(self.manifest["claims"]["automated_lkt_import"])
        self.assertFalse(self.manifest["claims"]["scientific_result"])

    def test_page_is_the_deterministic_target_render(self) -> None:
        item = collector.item_from_manifest(SLUG)
        self.assertEqual(collector.render_page(item), PAGE_PATH.read_text(encoding="utf-8"))
        page = PAGE_PATH.read_text(encoding="utf-8")
        self.assertIn('id="transcript-search"', page)
        self.assertIn("Search all 45 aligned transcript strings.", page)
        self.assertIn("not an automated Local Knowledge Terminal import or a customer result", page)
        self.assertNotIn("<ruby><ruby>", page)


if __name__ == "__main__":
    unittest.main()
