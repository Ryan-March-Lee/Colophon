import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from app import metadata


class MetadataUnitTests(unittest.TestCase):
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
        lookup.assert_called_once_with("10.1234/local.test", "", "")


if __name__ == "__main__":
    unittest.main()
