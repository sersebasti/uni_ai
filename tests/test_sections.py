import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import chromadb
import fitz
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src import index_documents, preprocess_pdfs, web_app
from src.sections import SECTIONS, Section, section_config
from src.startup import Startup


class SectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.pdf_root = root / "pdf"
        self.theory_dir = self.pdf_root / "teoria"
        self.practice_dir = self.pdf_root / "pratica"
        self.theory_dir.mkdir(parents=True)
        self.practice_dir.mkdir()
        self.write_pdf(self.pdf_root / "legacy.pdf", "THEORY legacy lecture")
        self.write_pdf(self.theory_dir / "common.pdf", "THEORY new lecture")
        self.write_pdf(self.practice_dir / "common.pdf", "PRACTICE Solid Edge manual")
        self.configs = {
            Section.THEORY: replace(
                section_config(Section.THEORY), input_dir=self.pdf_root, output_dir=root / "processed"
            ),
            Section.PRACTICE: replace(
                section_config(Section.PRACTICE), input_dir=self.practice_dir,
                output_dir=root / "processed" / "pratica",
            ),
        }
        self.client = chromadb.EphemeralClient()
        self.addCleanup(self.cleanup_collections)
        patches = [
            patch.dict(SECTIONS, self.configs),
            patch.object(preprocess_pdfs, "INPUT_DIR", self.pdf_root),
            patch.object(preprocess_pdfs, "OUTPUT_DIR", self.configs[Section.THEORY].output_dir),
            patch.object(index_documents, "MANIFEST_PATH", self.configs[Section.THEORY].manifest_path),
            patch.object(index_documents, "chroma_client", return_value=self.client),
            patch.object(index_documents, "embedding_model_digest", return_value="fixed-digest"),
            patch.object(index_documents, "create_embeddings", side_effect=lambda docs: [[1.0, 0.0]] * len(docs)),
            patch.object(web_app, "chroma_client", return_value=self.client),
            patch.object(web_app, "create_embeddings", return_value=[[1.0, 0.0]]),
            patch.object(web_app, "DOCUMENTS_DIR", self.pdf_root),
            patch.object(Startup, "wait_for_services"),
            patch.object(Startup, "prepare_model"),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def cleanup_collections(self):
        for collection in self.client.list_collections():
            self.client.delete_collection(collection.name)

    @staticmethod
    def write_pdf(path, text):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), (text + " describes procedures and technical drawing.\n") * 8)
            pdf.save(path)

    def prepare(self, section):
        startup = Startup(section)
        startup.run()
        self.assertTrue(startup.snapshot()["ready"], startup.snapshot())
        return startup

    def test_theory_and_practice_use_independent_indexes_and_caches(self):
        self.prepare(Section.THEORY)
        theory = self.configs[Section.THEORY]
        original_digest = preprocess_pdfs.file_digest(theory.manifest_path)
        original_mtime = theory.manifest_path.stat().st_mtime_ns
        self.prepare(Section.PRACTICE)
        self.assertEqual(preprocess_pdfs.file_digest(theory.manifest_path), original_digest)
        self.assertEqual(theory.manifest_path.stat().st_mtime_ns, original_mtime)
        self.assertEqual(self.client.get_collection(theory.collection_name).count(), 2)
        practice = self.configs[Section.PRACTICE]
        self.assertEqual(self.client.get_collection(practice.collection_name).count(), 1)
        self.write_pdf(self.practice_dir / "new.pdf", "PRACTICE new instructions")
        self.assertEqual(preprocess_pdfs.cached_page_count(theory.input_dir, theory.output_dir), 2)
        self.assertIsNone(preprocess_pdfs.cached_page_count(practice.input_dir, practice.output_dir))
        with (
            patch.object(preprocess_pdfs, "main") as preprocess,
            patch.object(index_documents, "main") as index,
        ):
            self.prepare(Section.THEORY)
        preprocess.assert_not_called()
        index.assert_not_called()

    def test_retrieval_never_mixes_sections(self):
        self.prepare(Section.THEORY)
        self.prepare(Section.PRACTICE)
        for section, expected, excluded in (
            (Section.THEORY, "THEORY", "PRACTICE"),
            (Section.PRACTICE, "PRACTICE", "THEORY"),
        ):
            sources = web_app.find_sources("Spiega questa procedura", section)
            self.assertTrue(all(expected in source["text"] for source in sources))
            self.assertTrue(all(excluded not in source["text"] for source in sources))

    def test_empty_practice_does_not_block_theory(self):
        (self.practice_dir / "common.pdf").unlink()
        theory = self.prepare(Section.THEORY)
        practice = Startup(Section.PRACTICE)
        practice.run()
        self.assertEqual(practice.snapshot()["status"], "empty")
        self.assertIsNone(practice.snapshot()["error"])
        self.assertTrue(theory.snapshot()["ready"])
        with patch.object(Startup, "run"), TestClient(web_app.app) as client:
            web_app.app.state.startup = theory
            web_app.app.state.startups = {Section.THEORY: theory, Section.PRACTICE: practice}
            self.assertTrue(client.get("/api/status").json()["ready"])
            self.assertEqual(client.get("/api/status?section=practice").json()["status"], "empty")
            with patch.object(web_app, "OpenAI") as openai:
                response = client.post("/api/chat", json={"question": "Come creo un foro?", "section": "practice"})
                self.assertEqual(response.status_code, 503)
                self.assertIn("Nessun PDF", response.json()["detail"])
                response = client.post("/api/illustrations", json={
                    "question": "Come creo un foro?", "answer": "Risposta di prova", "section": "practice",
                })
                self.assertEqual(response.status_code, 503)
                openai.assert_not_called()

    def test_failed_practice_does_not_invalidate_ready_theory(self):
        theory = self.prepare(Section.THEORY)
        (self.practice_dir / "common.pdf").write_bytes(b"invalid PDF")
        practice = Startup(Section.PRACTICE)
        with self.assertLogs("src.startup", level="ERROR"):
            practice.run()
        self.assertEqual(practice.snapshot()["status"], "error")
        self.assertTrue(theory.snapshot()["ready"])
        self.assertIsNotNone(index_documents.cached_chunk_count())

    def test_empty_theory_does_not_block_practice(self):
        (self.pdf_root / "legacy.pdf").unlink()
        (self.theory_dir / "common.pdf").unlink()
        theory = Startup(Section.THEORY)
        theory.run()
        self.assertEqual(theory.snapshot()["status"], "empty")
        self.prepare(Section.PRACTICE)

    def test_document_links_disambiguate_identical_names_between_sections(self):
        theory_pdf = web_app.document("common.pdf", Section.THEORY)
        practice_pdf = web_app.document("common.pdf", Section.PRACTICE)
        self.assertEqual(theory_pdf.path, self.theory_dir / "common.pdf")
        self.assertEqual(practice_pdf.path, self.practice_dir / "common.pdf")
        self.assertEqual(web_app.document("legacy.pdf").path, self.pdf_root / "legacy.pdf")
        with patch.object(Startup, "run"), TestClient(web_app.app) as client:
            for section, path in (
                ("theory", self.theory_dir / "common.pdf"),
                ("practice", self.practice_dir / "common.pdf"),
            ):
                response = client.get(f"/api/documents/common.pdf?section={section}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, path.read_bytes())
        with self.assertRaises(HTTPException) as raised:
            web_app.document("legacy.pdf", Section.PRACTICE)
        self.assertEqual(raised.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            web_app.document("../common.pdf", Section.PRACTICE)
        self.write_pdf(self.pdf_root / "common.pdf", "Duplicate theory name")
        with self.assertRaisesRegex(ValueError, "stesso nome"):
            preprocess_pdfs.find_pdf_paths(self.pdf_root)
        with self.assertRaises(HTTPException) as raised:
            web_app.document("common.pdf", Section.THEORY)
        self.assertEqual(raised.exception.status_code, 409)

    def test_json_and_multipart_chat_keep_section_and_cad_context(self):
        sources = [{
            "text": "Procedure from the manual", "lecture": "common",
            "source_pdf": str(self.practice_dir / "common.pdf"), "page": 1,
        }]
        with (
            patch.object(Startup, "run"),
            patch.object(web_app, "require_ready"),
            patch.object(web_app, "find_sources", return_value=sources) as find,
            patch.object(web_app, "OpenAI") as openai,
            TestClient(web_app.app) as client,
        ):
            completion = openai.return_value.chat.completions.create
            completion.return_value.choices = [Mock(message=Mock(content="Procedura numerata"))]
            body = {
                "question": "Come creo un foro?", "section": "practice",
                "cad_version": "2025", "cad_language": "English",
            }
            for multipart in (False, True):
                with self.subTest(multipart=multipart):
                    response = (
                        client.post("/api/chat", data=body, files={"image": ("screen.png", b"png", "image/png")})
                        if multipart else client.post("/api/chat", json=body)
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    find.assert_called_with(body["question"], Section.PRACTICE)
                    messages = completion.call_args.kwargs["messages"]
                    self.assertIn("procedura numerata", messages[0]["content"])
                    self.assertIn("Non inventare percorsi dei menu", messages[0]["content"])
                    text = messages[1]["content"][0]["text"] if multipart else messages[1]["content"]
                    self.assertIn("2025", text)
                    self.assertIn("English", text)
                    self.assertIn("?section=practice#page=1", response.json()["sources"][0]["document_url"])
            response = client.post("/api/chat", json={"question": "Spiega la quotatura"})
            self.assertEqual(response.status_code, 200)
            find.assert_called_with("Spiega la quotatura", Section.THEORY)
            self.assertNotIn("tutor pratico", completion.call_args.kwargs["messages"][0]["content"])

    def test_invalid_sections_are_rejected_instead_of_defaulting_to_theory(self):
        with patch.object(Startup, "run"), TestClient(web_app.app) as client:
            self.assertEqual(client.get("/api/status?section=unknown").status_code, 422)
            self.assertEqual(client.get("/api/documents/common.pdf?section=unknown").status_code, 422)
            for kwargs in (
                {"json": {"question": "Domanda valida", "section": "unknown"}},
                {"data": {"question": "Domanda valida", "section": "unknown"}},
            ):
                self.assertEqual(client.post("/api/chat", **kwargs).status_code, 422)
            self.assertEqual(client.post("/api/illustrations", json={
                "question": "Domanda valida", "answer": "Risposta valida", "section": "unknown",
            }).status_code, 422)

    def test_practical_attachments_keep_type_and_size_limits(self):
        with (
            patch.object(Startup, "run"),
            patch.object(web_app, "OpenAI") as openai,
            TestClient(web_app.app) as client,
        ):
            body = {"question": "Come creo un foro?", "section": "practice"}
            response = client.post(
                "/api/chat", data=body, files={"image": ("screen.gif", b"gif", "image/gif")},
            )
            self.assertEqual(response.status_code, 415)
            response = client.post(
                "/api/chat", data=body,
                files={"image": ("large.png", b"x" * (web_app.MAX_IMAGE_BYTES + 1), "image/png")},
            )
            self.assertEqual(response.status_code, 413)
            openai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
