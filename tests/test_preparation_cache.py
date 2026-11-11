import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import fitz
import chromadb
from chromadb.errors import NotFoundError

from src import index_documents, preprocess_pdfs
from src.rag import embedding_model_digest
from src.startup import Startup
from src.sections import SECTIONS, Section, section_config


class PreparationCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.input = root / "pdf"
        self.output = root / "processed"
        self.input.mkdir()
        configs = patch.dict(SECTIONS, {
            Section.THEORY: replace(
                section_config(Section.THEORY), input_dir=self.input, output_dir=self.output
            ),
        })
        configs.start()
        self.addCleanup(configs.stop)
        self.pdf_path = self.input / "lecture.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "A university lecture about technical drawing.\n" * 8)
            pdf.save(self.pdf_path)
        for name, value in (("INPUT_DIR", self.input), ("OUTPUT_DIR", self.output)):
            setting = patch.object(preprocess_pdfs, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        manifest = patch.object(index_documents, "MANIFEST_PATH", self.output / "pages.jsonl")
        manifest.start()
        self.addCleanup(manifest.stop)
        model = patch.object(index_documents, "embedding_model_digest", return_value="digest-one")
        model.start()
        self.addCleanup(model.stop)

    def preprocess(self, *options):
        preprocess_pdfs.main([
            "--input-dir", str(self.input), "--output-dir", str(self.output), *options,
        ])

    def test_completed_preprocessing_can_be_reused_after_restart(self):
        self.preprocess()
        self.assertEqual(preprocess_pdfs.cached_page_count(), 1)
        with patch.dict(os.environ, {"OPENAI_IMAGE_MODEL": "different", "OPENAI_MODEL": "different"}):
            self.assertEqual(preprocess_pdfs.cached_page_count(), 1)

    def test_pdf_content_changes_are_detected_even_if_mtime_is_preserved(self):
        self.preprocess()
        stat = self.pdf_path.stat()
        self.pdf_path.write_bytes(self.pdf_path.read_bytes() + b"\n% changed\n")
        os.utime(self.pdf_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertIsNone(preprocess_pdfs.cached_page_count())

    def test_added_and_removed_pdfs_invalidate_preprocessing(self):
        self.preprocess()
        extra = self.input / "extra.pdf"
        extra.write_bytes(self.pdf_path.read_bytes())
        self.assertIsNone(preprocess_pdfs.cached_page_count())
        extra.unlink()
        self.assertEqual(preprocess_pdfs.cached_page_count(), 1)
        self.pdf_path.unlink()
        self.assertIsNone(preprocess_pdfs.cached_page_count())

    def test_modified_manifest_and_missing_marker_are_not_reused(self):
        self.preprocess()
        manifest = self.output / "pages.jsonl"
        manifest.write_text(manifest.read_text() + "\n")
        self.assertIsNone(preprocess_pdfs.cached_page_count())
        self.preprocess()
        (self.output / "preprocess-state.json").unlink()
        self.assertIsNone(preprocess_pdfs.cached_page_count())

    def test_ocr_settings_and_pipeline_version_invalidate_preprocessing(self):
        self.preprocess("--ocr-dpi", "200")
        self.assertIsNone(preprocess_pdfs.cached_page_count())
        self.preprocess()
        with patch.object(preprocess_pdfs, "PREPROCESS_VERSION", 999):
            self.assertIsNone(preprocess_pdfs.cached_page_count())

    def test_corrupted_marker_is_logged_and_not_reused(self):
        self.preprocess()
        (self.output / "preprocess-state.json").write_text("{")
        with self.assertLogs("src.preprocess_pdfs", level="WARNING"):
            self.assertIsNone(preprocess_pdfs.cached_page_count())

    def test_failed_preprocessing_never_publishes_a_completion_marker(self):
        with patch.object(preprocess_pdfs, "process_pdf", side_effect=RuntimeError("OCR failed")):
            with self.assertRaisesRegex(RuntimeError, "OCR failed"):
                self.preprocess()
        self.assertFalse((self.output / "preprocess-state.json").exists())
        self.assertIsNone(preprocess_pdfs.cached_page_count())

    def test_pdf_changes_during_preprocessing_do_not_publish_results(self):
        process_pdf = preprocess_pdfs.process_pdf

        def mutate_after_processing(*args):
            records = process_pdf(*args)
            self.pdf_path.write_bytes(self.pdf_path.read_bytes() + b"\n% changed\n")
            return records

        with patch.object(preprocess_pdfs, "process_pdf", side_effect=mutate_after_processing):
            with self.assertRaisesRegex(RuntimeError, "cambiati"):
                self.preprocess()
        self.assertFalse((self.output / "pages.jsonl").exists())
        self.assertFalse((self.output / "preprocess-state.json").exists())

    def test_pdf_added_during_preprocessing_is_detected(self):
        process_pdf = preprocess_pdfs.process_pdf

        def add_pdf_after_processing(*args):
            records = process_pdf(*args)
            (self.input / "extra.pdf").write_bytes(self.pdf_path.read_bytes())
            return records

        with patch.object(preprocess_pdfs, "process_pdf", side_effect=add_pdf_after_processing):
            with self.assertRaisesRegex(RuntimeError, "cambiati"):
                self.preprocess()
        self.assertFalse((self.output / "preprocess-state.json").exists())

    def test_missing_page_render_invalidates_preprocessing(self):
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.insert_text((72, 72), "A short heading")
            pdf.save(self.pdf_path)
        with patch.object(preprocess_pdfs, "ocr_page", return_value="Recognized text"):
            self.preprocess()
        self.assertEqual(preprocess_pdfs.cached_page_count(), 1)
        next((self.output / "renders").rglob("*.png")).unlink()
        self.assertIsNone(preprocess_pdfs.cached_page_count())

    def test_index_fingerprint_depends_on_text_chunking_and_embedding_not_chat(self):
        self.preprocess()
        signature = index_documents.index_fingerprint()
        with patch.dict(os.environ, {"OPENAI_IMAGE_MODEL": "other", "OPENAI_MODEL": "other"}):
            self.assertEqual(index_documents.index_fingerprint(), signature)
        with patch.object(index_documents, "CHUNK_SIZE", 600):
            self.assertNotEqual(index_documents.index_fingerprint(), signature)
        with patch.object(index_documents, "embedding_model_digest", return_value="digest-two"):
            self.assertNotEqual(index_documents.index_fingerprint(), signature)
        with patch.dict(os.environ, {"OLLAMA_EMBEDDING_MODEL": "another-model"}):
            self.assertNotEqual(index_documents.index_fingerprint(), signature)
        (self.output / "pages.jsonl").write_text("changed")
        self.assertNotEqual(index_documents.index_fingerprint(), signature)

    def test_only_complete_matching_index_is_reused(self):
        self.preprocess()
        collection = Mock()
        collection.metadata = {"index_fingerprint": index_documents.index_fingerprint(), "indexed_chunks": 2}
        collection.count.return_value = 2
        with patch.object(index_documents, "chroma_client") as client:
            client.return_value.get_collection.return_value = collection
            self.assertEqual(index_documents.cached_chunk_count(), 2)
            collection.count.return_value = 1
            self.assertIsNone(index_documents.cached_chunk_count())
            collection.count.return_value = 2
            collection.metadata = {"hnsw:space": "cosine"}
            self.assertIsNone(index_documents.cached_chunk_count())
            client.return_value.get_collection.side_effect = NotFoundError("missing")
            self.assertIsNone(index_documents.cached_chunk_count())

    def test_interrupted_index_is_not_marked_complete(self):
        self.preprocess()
        with (
            patch.object(index_documents, "chroma_client") as client,
            patch.object(index_documents, "create_embeddings", side_effect=RuntimeError("embedding failed")),
        ):
            collection = client.return_value.create_collection.return_value
            with self.assertRaisesRegex(RuntimeError, "embedding failed"):
                index_documents.main()
            collection.modify.assert_not_called()

    def test_real_index_is_reused_by_two_startups_without_reprocessing(self):
        client = chromadb.EphemeralClient()
        self.addCleanup(client.delete_collection, index_documents.COLLECTION_NAME)
        with (
            patch.object(index_documents, "chroma_client", return_value=client),
            patch.object(index_documents, "create_embeddings", side_effect=lambda docs: [[1.0, 0.0]] * len(docs)),
            patch.object(Startup, "wait_for_services"),
            patch.object(Startup, "prepare_model"),
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        ):
            cold = Startup()
            cold.run()
            self.assertTrue(cold.snapshot()["ready"])
            self.assertEqual(preprocess_pdfs.cached_page_count(), 1)
            self.assertEqual(index_documents.cached_chunk_count(), 1)
            original_mtime = (self.output / "pages.jsonl").stat().st_mtime_ns
            with (
                patch.object(preprocess_pdfs, "main") as preprocess,
                patch.object(index_documents, "main") as index,
            ):
                first = Startup()
                first.run()
                with patch.dict(os.environ, {"OPENAI_IMAGE_MODEL": "changed-image-model"}):
                    second = Startup()
                    second.run()
            self.assertTrue(first.snapshot()["ready"])
            self.assertTrue(second.snapshot()["ready"])
            preprocess.assert_not_called()
            index.assert_not_called()
            self.assertEqual((self.output / "pages.jsonl").stat().st_mtime_ns, original_mtime)

    def test_embedding_model_digest_uses_the_installed_model(self):
        response = Mock()
        response.json.return_value = {"models": [{"name": "bge-m3:latest", "digest": "actual-digest"}]}
        with (
            patch("src.rag.httpx.get", return_value=response),
            patch.dict(os.environ, {"OLLAMA_EMBEDDING_MODEL": "bge-m3"}),
        ):
            self.assertEqual(embedding_model_digest(), "actual-digest")
            response.json.return_value = {"models": []}
            with self.assertRaisesRegex(RuntimeError, "non disponibile"):
                embedding_model_digest()


if __name__ == "__main__":
    unittest.main()
