import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError

from src.web_app import IllustrationRequest, generate_illustration


class IllustrationTests(unittest.TestCase):
    def setUp(self):
        self.request = IllustrationRequest(question="Spiega le proiezioni", answer="Descrizione delle viste")
        self.ready = patch("src.web_app.require_ready")
        self.ready.start()
        self.addCleanup(self.ready.stop)
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.openai_patch = patch("src.web_app.OpenAI")
        self.generate = self.openai_patch.start().return_value.images.generate
        self.addCleanup(self.openai_patch.stop)

    def test_provider_errors_have_actionable_messages_without_exposing_raw_body(self):
        for status, code, expected in (
            (400, "billing_hard_limit_reached", "fatturazione"),
            (401, "invalid_api_key", "chiave API"),
            (403, None, "organizzazione"),
            (404, "model_not_found", "OPENAI_IMAGE_MODEL"),
            (429, "insufficient_quota", "fatturazione"),
            (429, "rate_limit_exceeded", "Limite di richieste"),
            (500, None, "HTTP 500"),
        ):
            with self.subTest(status=status, code=code):
                response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1/images/generations"))
                self.generate.side_effect = APIStatusError(
                    "sensitive provider message", response=response,
                    body={"code": code, "message": "sensitive provider message"},
                )
                with self.assertLogs("src.web_app", level="WARNING"):
                    with self.assertRaises(HTTPException) as raised:
                        generate_illustration(self.request)
                self.assertEqual(raised.exception.status_code, 502)
                self.assertIn(expected, raised.exception.detail)
                self.assertNotIn("sensitive", raised.exception.detail)

    def test_timeout_and_connection_errors_are_explicit(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
        for error, status in (
            (APITimeoutError(request=request), 504),
            (APIConnectionError(request=request), 502),
        ):
            with self.subTest(error=type(error).__name__):
                self.generate.side_effect = error
                with self.assertLogs("src.web_app", level="WARNING"):
                    with self.assertRaises(HTTPException) as raised:
                        generate_illustration(self.request)
                self.assertEqual(raised.exception.status_code, status)

    def test_empty_and_malformed_image_data_are_explicit_errors(self):
        for data in (None, [], [Mock(b64_json=None)], [Mock(b64_json="not base64!")]):
            with self.subTest(data=data):
                self.generate.return_value = Mock(data=data)
                with self.assertLogs("src.web_app", level="ERROR"):
                    with self.assertRaises(HTTPException) as raised:
                        generate_illustration(self.request)
                self.assertEqual(raised.exception.status_code, 502)

    def test_successful_image_is_saved_and_returned(self):
        image = b"image data for storage test"
        self.generate.return_value = Mock(data=[Mock(b64_json=base64.b64encode(image).decode())])
        with tempfile.TemporaryDirectory() as directory:
            with patch("src.web_app.MEDIA_DIR", Path(directory)):
                result = generate_illustration(self.request)
            saved = Path(directory) / result["image_url"].split("/")[-1]
            self.assertEqual(saved.read_bytes(), image)
        self.assertTrue(result["image_url"].startswith("/media/illustration-"))
        self.generate.assert_called_once()

    def test_storage_failure_is_reported(self):
        self.generate.return_value = Mock(data=[Mock(b64_json="aW1hZ2U=")])
        with (
            patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")),
            self.assertLogs("src.web_app", level="ERROR"),
        ):
            with self.assertRaises(HTTPException) as raised:
                generate_illustration(self.request)
        self.assertEqual(raised.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
