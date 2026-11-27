import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from app import ai, metadata


class PdfAbstractTests(unittest.TestCase):
    def test_ieee_abstract_excludes_publication_notes_and_index_terms(self):
        text = (
            "Title and authors\nAbstract—This study measured the effect of\n"
            "different layouts on signal loss.\n"
            "Received 15 May 2026; revised 4 August 2026.\n"
            "Index Terms—layout, measurement\nI. INTRODUCTION\nBody text."
        )
        self.assertEqual(
            metadata.extract_abstract(text),
            "This study measured the effect of different layouts on signal loss.",
        )

    def test_chinese_abstract_excludes_keywords(self):
        self.assertEqual(
            metadata.extract_abstract("摘 要：本文分析了不同条件下测量结果的变化。\n关键词：测量；分析"),
            "本文分析了不同条件下测量结果的变化。",
        )

    def test_standalone_heading_stops_at_numbered_section(self):
        self.assertEqual(
            metadata.extract_abstract("A B S T R A C T\nThis study reports new measurements.\n1. Introduction\nBody text."),
            "This study reports new measurements.",
        )

    def test_ocr_abstract_headings_and_keyword_heading(self):
        cases = (
            "Abstmct- This study evaluates the proposed model.\nI. INTRODUCTION\nBody.",
            "Abstruct- This study evaluates the proposed model.\nI. INTRODUCTION\nBody.",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    metadata.extract_abstract(text),
                    "This study evaluates the proposed model.",
                )
        self.assertEqual(
            metadata.extract_abstract(
                "Abstract- This study evaluates the proposed model.\n"
                "Kryuiordi- impedance matching, gradient\nI. INTRODUCTION\nBody."
            ),
            "This study evaluates the proposed model.",
        )

    def test_missing_or_incomplete_abstract_is_empty(self):
        for text in ("", "Abstract methods were used to analyze the data.", "Abstract\nThis fragment is incomplete"):
            with self.subTest(text=text):
                self.assertEqual(metadata.extract_abstract(text), "")


class MetadataUnitTests(unittest.TestCase):
    def test_ai_metadata_parser_accepts_abstract_only(self):
        parsed = ai._parse_meta_json('{"abstract":"A directly extracted abstract."}')
        self.assertEqual(parsed["abstract"], "A directly extracted abstract.")

    def test_abstract_is_merged_only_when_missing(self):
        base = {"abstract": ""}
        added = metadata._merge_meta(
            base,
            {"abstract": "Online abstract."},
            {},
        )
        self.assertEqual(base["abstract"], "Online abstract.")
        self.assertIn("abstract", added)

        metadata._merge_meta(base, {"abstract": "Replacement."}, {})
        self.assertEqual(base["abstract"], "Online abstract.")

    def test_missing_abstract_queries_openalex_after_crossref_hit(self):
        crossref = {
            "source": "Crossref",
            "title": "A paper title",
            "abstract": "",
            "authors": ["A Author"],
            "orgs": ["Example University"],
            "year": 2024,
            "venue": "Journal",
            "doi": "10.1234/test",
            "arxiv_id": "",
            "url": "https://doi.org/10.1234/test",
        }
        openalex = {
            **crossref,
            "source": "OpenAlex",
            "abstract": "Abstract provided by OpenAlex.",
        }

        with patch.object(metadata, "fetch_crossref", return_value=crossref) as crossref_fetch, \
             patch.object(metadata, "fetch_openalex", return_value=openalex) as openalex_fetch:
            result = metadata.resolve(
                filename="10.1234/unrelated.pdf",
                path=None,
                known_doi="10.1234/test",
                current={
                    "title": "A paper title",
                    "authors": ["A Author"],
                    "orgs": ["Example University"],
                    "year": 2024,
                    "venue": "Journal",
                    "doi": "10.1234/test",
                    "abstract": "",
                },
                use_ai=False,
            )

        self.assertEqual(result["meta"]["abstract"], "Abstract provided by OpenAlex.")
        self.assertIn("OpenAlex", result["source"])
        self.assertEqual(result["missing"], [])
        crossref_fetch.assert_called_once_with("10.1234/test")
        openalex_fetch.assert_called_once_with("10.1234/test")

    def test_missing_abstract_uses_single_item_ai_only_after_online_sources(self):
        current = {
            "title": "A paper title",
            "authors": ["A Author"],
            "orgs": ["Example University"],
            "year": 2024,
            "venue": "Journal",
            "doi": "10.1234/test",
        }
        with NamedTemporaryFile(suffix=".pdf") as file, \
             patch.object(metadata, "read_pdf_info", return_value={}), \
             patch.object(metadata, "pdf_text", return_value="Unlabeled paragraph. " * 20), \
             patch.object(metadata, "fetch_crossref", side_effect=TimeoutError) as crossref_fetch, \
             patch.object(metadata, "fetch_openalex", return_value=None) as openalex_fetch, \
             patch.object(ai, "extract_metadata", return_value=(
                 ai._parse_meta_json('{"abstract":"A directly extracted abstract."}'),
                 "test-model",
             )) as extract:
            kwargs = dict(filename="paper.pdf", path=Path(file.name), current=current, conn=object())
            batch = metadata.resolve(**kwargs, use_ai=False)
            extract.assert_not_called()
            self.assertEqual(batch["missing"], ["abstract"])
            crossref_fetch.reset_mock()
            openalex_fetch.reset_mock()

            single = metadata.resolve(**kwargs)

        self.assertEqual(single["meta"]["abstract"], "A directly extracted abstract.")
        self.assertEqual(single["missing"], [])
        self.assertTrue(single["needsCheck"])
        extract.assert_called_once()
        crossref_fetch.assert_called_once_with("10.1234/test")
        openalex_fetch.assert_called_once_with("10.1234/test")

    def test_crossref_affiliations_are_deduplicated(self):
        message = {
            "title": ["A paper title"],
            "author": [
                {
                    "given": "A",
                    "family": "Author",
                    "affiliation": [{"name": "Example University"}],
                },
                {
                    "given": "B",
                    "family": "Author",
                    "affiliation": [{"name": "Example University"}],
                },
            ],
            "issued": {"date-parts": [[2024]]},
            "container-title": ["Journal"],
            "DOI": "10.1234/test",
        }

        result = metadata._crossref_meta(message, "10.1234/test")

        self.assertEqual(result["orgs"], ["Example University"])

    def test_crossref_hit_still_enriches_units_from_openalex(self):
        crossref = {
            "source": "Crossref",
            "title": "A paper title",
            "abstract": "",
            "authors": ["A Author"],
            "orgs": [],
            "year": 2024,
            "venue": "Journal",
            "doi": "10.1234/test",
            "arxiv_id": "",
            "url": "https://doi.org/10.1234/test",
        }
        openalex = {
            "source": "OpenAlex",
            "title": "A paper title",
            "abstract": "",
            "authors": ["A Author"],
            "orgs": ["Example University"],
            "year": 2024,
            "venue": "Journal",
            "doi": "10.1234/test",
            "arxiv_id": "",
            "url": "https://doi.org/10.1234/test",
        }

        with patch.object(metadata, "fetch_crossref", return_value=crossref) as crossref_fetch, \
             patch.object(metadata, "fetch_openalex", return_value=openalex) as openalex_fetch:
            result = metadata.resolve(
                filename="paper.pdf",
                path=None,
                known_doi="10.1234/test",
                current={
                    "title": "A paper title",
                    "authors": ["A Author"],
                    "year": 2024,
                    "venue": "Journal",
                    "doi": "10.1234/test",
                    "arxiv_id": "",
                },
                use_ai=False,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["meta"]["orgs"], ["Example University"])
        self.assertEqual(crossref_fetch.call_count, 1)
        self.assertEqual(openalex_fetch.call_count, 1)

    def test_complete_local_text_skips_online_lookup(self):
        text = """IEEE JOURNAL OF LOCAL TESTS, VOL. 1, JUNE 2024
Local Text Layer Metadata Test
Alice Smith and Bob Jones
Department of Electrical Engineering, Example University
DOI: 10.1234/local.test
ABSTRACT This is a local abstract.
"""

        with NamedTemporaryFile(suffix=".pdf") as file, \
             patch.object(metadata, "read_pdf_info", return_value={}), \
             patch.object(metadata, "pdf_text", return_value=text), \
             patch.object(metadata, "_lookup_online") as lookup:
            result = metadata.resolve(
                filename="paper.pdf",
                path=Path(file.name),
                use_ai=False,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["meta"]["title"], "Local Text Layer Metadata Test")
        self.assertEqual(result["meta"]["authors"], ["Alice Smith", "Bob Jones"])
        self.assertEqual(result["meta"]["abstract"], "This is a local abstract.")
        self.assertEqual(result["meta"]["orgs"], [
            "Department of Electrical Engineering, Example University",
        ])
        lookup.assert_not_called()

    def test_incomplete_local_text_queries_online_only_for_missing_fields(self):
        text = """Local Text Layer Metadata Test
Alice Smith and Bob Jones
Department of Electrical Engineering, Example University
DOI: 10.1234/local.test
ABSTRACT This is a local abstract.
"""
        online = {
            "source": "Crossref",
            "title": "Online Title Must Not Replace Local Title",
            "abstract": "",
            "authors": ["Online Author"],
            "orgs": ["Online University"],
            "year": 2024,
            "venue": "Online Journal",
            "doi": "10.1234/local.test",
            "arxiv_id": "",
            "url": "https://doi.org/10.1234/local.test",
        }

        with NamedTemporaryFile(suffix=".pdf") as file, \
             patch.object(metadata, "read_pdf_info", return_value={}), \
             patch.object(metadata, "pdf_text", return_value=text), \
             patch.object(metadata, "_lookup_online", return_value=(online, [])) as lookup, \
             patch.object(metadata, "_enrich_online"):
            result = metadata.resolve(
                filename="paper.pdf",
                path=Path(file.name),
                use_ai=False,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["meta"]["title"], "Local Text Layer Metadata Test")
        self.assertEqual(result["meta"]["authors"], ["Alice Smith", "Bob Jones"])
        self.assertEqual(result["meta"]["orgs"], [
            "Department of Electrical Engineering, Example University",
        ])
        self.assertEqual(result["meta"]["year"], 2024)
        self.assertEqual(result["meta"]["venue"], "Online Journal")
        lookup.assert_called_once_with("10.1234/local.test", "", "", set())


if __name__ == "__main__":
    unittest.main()
