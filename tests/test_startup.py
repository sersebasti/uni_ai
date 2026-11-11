import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import fitz
from fastapi.testclient import TestClient

from src import index_documents, preprocess_pdfs
from src.startup import STAGES, Startup
from src.web_app import app
from src.sections import SECTIONS, Section, section_config


class StartupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        input_dir = root / "pdf"
        input_dir.mkdir()
        (input_dir / "fixture.pdf").touch()
        configs = patch.dict(SECTIONS, {
            section: replace(
                section_config(section),
                input_dir=input_dir if section == Section.THEORY else input_dir / "pratica",
                output_dir=root / section.value,
            )
            for section in Section
        })
        configs.start()
        self.addCleanup(configs.stop)
        for target in (
            "src.startup.preprocess_pdfs.cached_page_count",
            "src.startup.index_documents.cached_chunk_count",
        ):
            cache = patch(target, return_value=None)
            cache.start()
            self.addCleanup(cache.stop)

    def test_preparation_reports_progress_and_becomes_ready(self):
        startup = Startup()

        def process(argv, progress):
            progress(1, 2, "Pagina 1")
            stage = startup.snapshot()["stages"][2]
            self.assertEqual((stage["completed"], stage["total"]), (1, 2))
            self.assertFalse(startup.snapshot()["ready"])
            progress(2, 2, "Pagina 2")

        with (
            patch.object(startup, "wait_for_services"),
            patch.object(startup, "prepare_model"),
            patch("src.startup.preprocess_pdfs.main", side_effect=process),
            patch("src.startup.index_documents.main") as index,
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        ):
            startup.run()
        self.assertTrue(startup.snapshot()["ready"])
        self.assertEqual(startup.snapshot()["status"], "ready")
        self.assertTrue(all(stage["status"] == "done" for stage in startup.snapshot()["stages"]))
        index.assert_called_once()

    def test_failed_preprocessing_is_visible_and_prevents_indexing(self):
        startup = Startup()
        with (
            patch.object(startup, "wait_for_services"),
            patch.object(startup, "prepare_model"),
            patch("src.startup.preprocess_pdfs.main", side_effect=SystemExit("PDF mancanti")),
            patch("src.startup.index_documents.main") as index,
            self.assertLogs("src.startup", level="ERROR"),
        ):
            startup.run()
        state = startup.snapshot()
        self.assertFalse(state["ready"])
        self.assertEqual(state["status"], "error")
        self.assertEqual(state["error"], "PDF mancanti")
        self.assertEqual(state["stages"][2]["status"], "error")
        index.assert_not_called()

    def test_missing_api_key_is_visible(self):
        startup = Startup()
        with (
            patch.object(startup, "wait_for_services"),
            patch.object(startup, "prepare_model"),
            patch("src.startup.preprocess_pdfs.main"),
            patch("src.startup.index_documents.main"),
            patch.dict(os.environ, {"OPENAI_API_KEY": ""}),
            self.assertLogs("src.startup", level="ERROR"),
        ):
            startup.run()
        self.assertFalse(startup.snapshot()["ready"])
        self.assertIn("OPENAI_API_KEY", startup.snapshot()["error"])

    def test_snapshot_is_isolated(self):
        startup = Startup()
        startup.snapshot()["stages"][0]["status"] = "done"
        self.assertEqual(startup.snapshot()["stages"][0]["status"], "pending")

    def test_model_download_reports_bytes_and_rejects_truncated_stream(self):
        startup = Startup()
        response = Mock()
        response.iter_lines.return_value = iter([
            json.dumps({"status": "pulling", "completed": 50, "total": 100}),
        ])
        stream = Mock()
        stream.__enter__ = Mock(return_value=response)
        stream.__exit__ = Mock(return_value=False)
        with patch("src.startup.httpx.stream", return_value=stream):
            with self.assertRaisesRegex(RuntimeError, "interrotto"):
                startup.prepare_model()
        stage = startup.snapshot()["stages"][1]
        self.assertEqual((stage["completed"], stage["total"]), (50, 100))

    def test_service_timeout_surfaces_failure(self):
        startup = Startup()
        with (
            patch("src.startup.httpx.get", side_effect=ConnectionError("offline")),
            patch("src.startup.time.monotonic", side_effect=[0, 181]),
        ):
            with self.assertRaisesRegex(RuntimeError, "180 secondi"):
                startup.wait_for_services()

    def test_ui_and_status_available_while_background_worker_is_blocked(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_services(startup):
            entered.set()
            release.wait(5)

        try:
            with (
                patch.object(Startup, "wait_for_services", slow_services),
                patch.object(Startup, "prepare_model"),
                patch("src.startup.preprocess_pdfs.main"),
                patch("src.startup.index_documents.main"),
                TestClient(app) as client,
            ):
                self.assertTrue(entered.wait(2))
                self.assertEqual(client.get("/").status_code, 200)
                response = client.get("/api/status")
                self.assertEqual(response.status_code, 200)
                state = response.json()
                self.assertEqual(state["status"], "preparing")
                self.assertEqual([stage["id"] for stage in state["stages"]], [s[0] for s in STAGES])
                self.assertEqual(client.post("/api/chat", json={"question": "test"}).status_code, 503)
                self.assertEqual(
                    client.post("/api/illustrations", json={"question": "test", "answer": "test"}).status_code,
                    503,
                )
                client.app.state.startup.stop()
                release.set()
        finally:
            release.set()

    def test_preprocess_reports_each_page_and_writes_complete_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "pdf"
            input_dir.mkdir()
            output_dir = root / "processed"
            with fitz.open() as pdf:
                for _ in range(2):
                    page = pdf.new_page()
                    page.insert_text((72, 72), "A university lecture. " * 10)
                pdf.save(input_dir / "lecture.pdf")
            progress = Mock()
            preprocess_pdfs.main(
                ["--input-dir", str(input_dir), "--output-dir", str(output_dir), "--skip-ocr"],
                progress=progress,
            )
            self.assertEqual([call.args[:2] for call in progress.call_args_list], [(0, 2), (1, 2), (2, 2)])
            records = (output_dir / "pages.jsonl").read_text().splitlines()
            self.assertEqual([json.loads(record)["page"] for record in records], [1, 2])
            self.assertFalse((output_dir / "pages.jsonl.tmp").exists())

    def test_index_reports_only_persisted_batches(self):
        chunks = [("text", {"source_pdf": "test.pdf", "page": n, "chunk": 1}) for n in range(33)]
        progress = Mock()
        client = Mock()
        with (
            patch.object(index_documents, "load_chunks", return_value=chunks),
            patch.object(index_documents, "index_fingerprint", return_value="fingerprint"),
            patch.object(index_documents, "chroma_client", return_value=client),
            patch.object(index_documents, "create_embeddings", side_effect=lambda docs: [[1.0]] * len(docs)),
        ):
            index_documents.main(progress=progress)
        self.assertEqual([call.args[:2] for call in progress.call_args_list], [(0, 33), (32, 33), (33, 33)])
        self.assertEqual(client.create_collection.return_value.upsert.call_count, 2)
        client.create_collection.return_value.modify.assert_called_once_with(
            metadata={"index_fingerprint": "fingerprint", "indexed_chunks": 33}
        )

    def test_cached_results_skip_ocr_and_embedding_on_web_restart(self):
        startup = Startup()
        with (
            patch.object(startup, "wait_for_services"),
            patch.object(startup, "prepare_model"),
            patch("src.startup.preprocess_pdfs.cached_page_count", return_value=468),
            patch("src.startup.index_documents.cached_chunk_count", return_value=517),
            patch("src.startup.preprocess_pdfs.main") as preprocess,
            patch("src.startup.index_documents.main") as index,
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_IMAGE_MODEL": "changed-model"}),
        ):
            startup.run()
        self.assertTrue(startup.snapshot()["ready"])
        preprocess.assert_not_called()
        index.assert_not_called()
        self.assertIn("Riutilizzate", startup.snapshot()["stages"][2]["detail"])
        self.assertIn("riutilizzato", startup.snapshot()["stages"][3]["detail"])

    def test_missing_index_rebuilds_without_repeating_preprocessing(self):
        startup = Startup()
        with (
            patch.object(startup, "wait_for_services"),
            patch.object(startup, "prepare_model"),
            patch("src.startup.preprocess_pdfs.cached_page_count", return_value=468),
            patch("src.startup.preprocess_pdfs.main") as preprocess,
            patch("src.startup.index_documents.main") as index,
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        ):
            startup.run()
        self.assertTrue(startup.snapshot()["ready"])
        preprocess.assert_not_called()
        index.assert_called_once()


if __name__ == "__main__":
    unittest.main()
