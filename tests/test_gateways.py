from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from infra.gateways import jina_reader


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "", payload: object | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class JinaReaderGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        jina_reader._last_request_time = 0.0

    def test_fetch_jina_text_returns_stripped_text(self) -> None:
        with patch("infra.gateways.jina_reader._throttle"), patch(
            "infra.gateways.jina_reader.requests.get",
            return_value=_FakeResponse(text="  Article body  "),
        ) as get:
            text = jina_reader.fetch_jina_text("https://example.com/article", timeout=5)

        self.assertEqual(text, "Article body")
        self.assertEqual(get.call_args.args[0], "https://r.jina.ai/https://example.com/article")
        self.assertEqual(get.call_args.kwargs["timeout"], 5)

    def test_fetch_jina_text_retries_429_retry_after(self) -> None:
        responses = [
            _FakeResponse(status_code=429, text='{"retryAfter": 2.5}', payload={"retryAfter": 2.5}),
            _FakeResponse(text="Recovered"),
        ]

        with patch("infra.gateways.jina_reader._throttle"), patch(
            "infra.gateways.jina_reader.time.sleep",
        ) as sleep, patch("infra.gateways.jina_reader.requests.get", side_effect=responses) as get:
            text = jina_reader.fetch_jina_text("https://example.com/article", max_retries=1)

        self.assertEqual(text, "Recovered")
        sleep.assert_called_once_with(2.5)
        self.assertEqual(get.call_count, 2)

    def test_fetch_jina_text_rejects_empty_content(self) -> None:
        with patch("infra.gateways.jina_reader._throttle"), patch(
            "infra.gateways.jina_reader.requests.get",
            return_value=_FakeResponse(text=" "),
        ):
            with self.assertRaises(jina_reader.JinaReaderError):
                jina_reader.fetch_jina_text("https://example.com/article")

    def test_fetch_jina_text_raises_on_non_retryable_status(self) -> None:
        with patch("infra.gateways.jina_reader._throttle"), patch(
            "infra.gateways.jina_reader.requests.get",
            return_value=_FakeResponse(status_code=500, text="server error"),
        ):
            with self.assertRaises(jina_reader.JinaReaderError):
                jina_reader.fetch_jina_text("https://example.com/article", max_retries=0)


class InfraBoundaryTest(unittest.TestCase):
    def test_dao_layer_stays_db_only(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        dao_files = sorted((repo_root / "infra" / "dao").glob("*.py"))

        self.assertTrue(dao_files)
        for path in dao_files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("infra.gateways", text, path.name)
            self.assertNotIn("requests", text, path.name)
            self.assertNotIn("httpx", text, path.name)
            self.assertNotIn("oss2", text, path.name)

    def test_legacy_infra_http_and_oss_modules_are_removed(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        self.assertFalse((repo_root / "infra" / "http.py").exists())
        self.assertFalse((repo_root / "infra" / "oss.py").exists())
        self.assertTrue((repo_root / "infra" / "gateways" / "http_transport.py").exists())
        self.assertTrue((repo_root / "infra" / "gateways" / "oss.py").exists())
