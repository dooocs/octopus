from __future__ import annotations

from typing import Any

import requests

try:
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
except ImportError:  # pragma: no cover
    retry = None  # type: ignore[assignment]
    retry_if_exception_type = None  # type: ignore[assignment]
    stop_after_attempt = None  # type: ignore[assignment]
    wait_exponential = None  # type: ignore[assignment]


class RetryableHttpError(Exception):
    pass


class HttpClient:
    def __init__(
        self,
        *,
        timeout: int | float = 15,
        retries: int = 3,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.headers = headers or {}
        self.session = requests.Session()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", self.timeout)
        headers = {**self.headers, **kwargs.pop("headers", {})}

        def _send_once() -> requests.Response:
            response = self.session.request(method, url, timeout=timeout, headers=headers, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504):
                raise RetryableHttpError(f"retryable HTTP {response.status_code}: {url}")
            return response

        if retry is None:
            last_error: Exception | None = None
            for _ in range(max(self.retries, 1)):
                try:
                    return _send_once()
                except (RetryableHttpError, requests.exceptions.Timeout) as exc:
                    last_error = exc
            assert last_error is not None
            raise last_error

        @retry(
            retry=retry_if_exception_type((RetryableHttpError, requests.exceptions.Timeout)),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            stop=stop_after_attempt(self.retries),
            reraise=True,
        )
        def _send() -> requests.Response:
            return _send_once()

        return _send()


def http_get(url: str, **kwargs: Any) -> requests.Response:
    retries = int(kwargs.pop("retries", 1))
    timeout = kwargs.get("timeout", 15)
    client = HttpClient(timeout=timeout, retries=max(retries, 1))
    return client.get(url, **kwargs)


def http_post(url: str, **kwargs: Any) -> requests.Response:
    retries = int(kwargs.pop("retries", 1))
    timeout = kwargs.get("timeout", 15)
    client = HttpClient(timeout=timeout, retries=max(retries, 1))
    return client.post(url, **kwargs)


def http_patch(url: str, **kwargs: Any) -> requests.Response:
    retries = int(kwargs.pop("retries", 1))
    timeout = kwargs.get("timeout", 15)
    client = HttpClient(timeout=timeout, retries=max(retries, 1))
    return client._request("PATCH", url, **kwargs)


def http_delete(url: str, **kwargs: Any) -> requests.Response:
    retries = int(kwargs.pop("retries", 1))
    timeout = kwargs.get("timeout", 15)
    client = HttpClient(timeout=timeout, retries=max(retries, 1))
    return client._request("DELETE", url, **kwargs)
