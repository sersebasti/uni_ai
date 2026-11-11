import asyncio
import os
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, patch

import fitz
from fastapi import HTTPException

from src.preprocess_pdfs import ocr_page
from src.web_app import ChatRequest, chat, find_sources, is_course_overview


class RetrievalTests(unittest.TestCase):
    def test_course_overview_detection_does_not_replace_specific_search(self):
        for question in (
            "di cosa parla questo corso universitario?",
            "Di cosa tratta il corso?",
            "Quali argomenti tratta il corso?",
            "Fammi una panoramica delle dispense",
        ):
            with self.subTest(question=question):
                self.assertTrue(is_course_overview(question))
        for question in ("Spiegami le proiezioni ortogonali", "Come funziona il CAD nel corso?"):
            self.assertFalse(is_course_overview(question))

    def test_overview_covers_each_document_once_in_chunk_order(self):
        collection = Mock()
        collection.get.return_value = {
            "documents": ["Second chunk", "Lecture two", "Course introduction"],
            "metadatas": [
                {"lecture": "L1", "source_pdf": "L1.pdf", "page": 1, "chunk": 2},
                {"lecture": "L2", "source_pdf": "L2.pdf", "page": 1, "chunk": 1},
                {"lecture": "L1", "source_pdf": "L1.pdf", "page": 1, "chunk": 1},
            ],
        }
        with (
            patch("src.web_app.chroma_client") as client,
            patch("src.web_app.create_embeddings") as embed,
        ):
            client.return_value.get_collection.return_value = collection
            sources = find_sources("di cosa parla questo corso universitario?")
        self.assertEqual([source["text"] for source in sources], ["Course introduction", "Lecture two"])
        collection.get.assert_called_once_with(where={"page": 1}, include=["documents", "metadatas"])
        collection.query.assert_not_called()
        embed.assert_not_called()

    def test_specific_question_still_uses_embeddings(self):
        collection = Mock()
        collection.query.return_value = {
            "documents": [["Technical explanation"]],
            "metadatas": [[{"lecture": "L2", "source_pdf": "L2.pdf", "page": 12, "chunk": 1}]],
        }
        with (
            patch("src.web_app.chroma_client") as client,
            patch("src.web_app.create_embeddings", return_value=[[0.5]]) as embed,
        ):
            client.return_value.get_collection.return_value = collection
            sources = find_sources("Spiegami le proiezioni ortogonali")
        self.assertEqual(sources[0]["page"], 12)
        embed.assert_called_once()
        collection.get.assert_not_called()

    def test_empty_overview_is_an_explicit_error(self):
        with patch("src.web_app.chroma_client") as client:
            collection = client.return_value.get_collection.return_value
            collection.get.return_value = {"documents": [], "metadatas": []}
            with self.assertRaises(HTTPException) as raised:
                find_sources("di cosa parla questo corso?")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("fonti utilizzabili", raised.exception.detail)

    def test_chat_keeps_chunk_context_but_deduplicates_page_links(self):
        sources = [
            {"text": "First part", "lecture": "L1", "source_pdf": "L1.pdf", "page": 1},
            {"text": "Second part", "lecture": "L1", "source_pdf": "L1.pdf", "page": 1},
        ]
        with (
            patch("src.web_app.require_ready"),
            patch(
                "src.web_app.parse_chat_request",
                new=AsyncMock(return_value=(ChatRequest(question="Domanda di prova"), None, None)),
            ),
            patch("src.web_app.find_sources", return_value=sources),
            patch("src.web_app.OpenAI") as openai,
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        ):
            completion = openai.return_value.chat.completions.create
            completion.return_value.choices = [Mock(message=Mock(content="Risposta di prova"))]
            result = asyncio.run(chat(Mock()))
        self.assertEqual(len(result["sources"]), 1)
        prompt = completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("First part", prompt)
        self.assertIn("Second part", prompt)

    def test_ocr_enables_orientation_detection_and_propagates_failure(self):
        page = Mock()
        page.get_pixmap.return_value.tobytes.return_value = b"png"
        with patch("src.preprocess_pdfs.subprocess.run") as run:
            run.return_value.stdout = b"Correct text"
            self.assertEqual(ocr_page(page, 300, "ita+eng"), "Correct text")
            self.assertEqual(run.call_args.args[0][-2:], ["--psm", "1"])
            run.side_effect = subprocess.CalledProcessError(1, "tesseract")
            with self.assertRaises(subprocess.CalledProcessError):
                ocr_page(page, 300, "ita+eng")

    def test_real_ocr_recovers_upside_down_text(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            text = (
                "DISEGNO TECNICO E GEOMETRIA\n"
                "Le proiezioni rappresentano oggetti nello spazio.\n"
                "Il corso introduce strumenti digitali e disegno assistito.\n"
                "La rappresentazione tecnica comunica forme e dimensioni.\n"
            )
            page.insert_text((550, 720), text * 5, fontsize=14, rotate=180)
            result = ocr_page(page, 200, "ita+eng").casefold()
        self.assertIn("disegno tecnico", result)
        self.assertIn("proiezioni", result)


if __name__ == "__main__":
    unittest.main()
