# Alibaba Cloud OSS image upload gateway.

from __future__ import annotations

import hashlib
import os
import time
from datetime import date
from urllib.parse import urlparse

from infra.gateways.http_transport import http_get

OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
OSS_BUCKET = "amazingindex"
OSS_CUSTOM_DOMAIN = "www.amazingindex.com"

_oss_bucket = None
_oss_enabled: bool | None = None

CONNECT_TIMEOUT = 10
DOWNLOAD_TIMEOUT = 30

_EXT_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
}


def _get_bucket():
    global _oss_bucket, _oss_enabled
    if _oss_enabled is not None:
        return _oss_bucket

    key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
    key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    endpoint = OSS_ENDPOINT
    bucket_name = OSS_BUCKET

    if not all([key_id, key_secret]):
        _oss_enabled = False
        return None

    try:
        import oss2

        auth = oss2.Auth(key_id, key_secret)
        _oss_bucket = oss2.Bucket(auth, endpoint, bucket_name)
        _oss_enabled = True
        return _oss_bucket
    except Exception as e:
        print(f"OSS init failed: {e}")
        _oss_enabled = False
        return None


def _guess_ext(url: str, mime_type: str = "") -> str:
    if mime_type:
        ext = _EXT_MAP.get(mime_type.split(";")[0].strip().lower())
        if ext:
            return ext

    path = urlparse(url).path
    _, ext = os.path.splitext(path)
    if ext and len(ext) <= 6:
        return ext

    if "svg" in url.lower():
        return ".svg"

    return ".png"


def _build_oss_key(url: str, ext: str, date_str: str) -> str:
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return f"image/{date_str}/{url_hash}{ext}"


def upload_image_to_oss(url: str, date_str: str | None = None) -> str | None:
    """Download image from url and upload to OSS.

    Returns the public OSS URL on success, or None on failure.
    """
    bucket = _get_bucket()
    if bucket is None:
        return None

    if not date_str:
        date_str = date.today().strftime("%Y%m%d")

    try:
        resp = None
        for attempt in range(3):
            resp = http_get(
                url,
                timeout=DOWNLOAD_TIMEOUT,
                stream=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ImageFetcher/1.0)",
                },
            )
            if resp.status_code == 200:
                break
            if attempt < 2:
                print(f"  Image download {resp.status_code}, retrying in 3s ({attempt + 1}/2): {url[:60]}")
                time.sleep(3)
        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp is not None else "empty"
            print(f"  Image download failed [{status}]: {url[:80]}")
            return None

        mime_type = resp.headers.get("Content-Type", "")
        image_data = resp.content

        if not image_data or len(image_data) < 100:
            return None

        ext = _guess_ext(url, mime_type)
        oss_key = _build_oss_key(url, ext, date_str)

        bucket.put_object(
            oss_key,
            image_data,
            headers={
                "Content-Type": mime_type or "application/octet-stream",
            },
        )

        return f"https://{OSS_CUSTOM_DOMAIN}/{oss_key}"

    except Exception as e:
        print(f"  OSS upload failed: {url[:80]} | {e}")
        return None


MAX_MEDIA_BYTES = 15 * 1024 * 1024


def archive_media(url: str, prefix: str) -> dict[str, object] | None:
    """Download `url` and keep it on OSS under `<prefix>/<sha256><ext>`.

    Content-addressed: the same bytes are stored once, whatever URL they came
    from, and an object that is already there is not uploaded again. Returns
    {"url", "key", "content_type", "bytes"}, or None when OSS is not configured
    or the download fails (the caller keeps the item without this file).
    """
    bucket = _get_bucket()
    if bucket is None:
        return None
    try:
        resp = None
        for attempt in range(3):
            resp = http_get(
                url,
                timeout=DOWNLOAD_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ImageFetcher/1.0)"},
            )
            if resp.status_code == 200:
                break
            if attempt < 2:
                time.sleep(2)
        if resp is None or resp.status_code != 200:
            print(f"  Media download failed [{resp.status_code if resp is not None else 'empty'}]: {url[:80]}")
            return None
        data = resp.content
        if not data or len(data) < 100 or len(data) > MAX_MEDIA_BYTES:
            print(f"  Media skipped (size {len(data or b'')} bytes): {url[:80]}")
            return None
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("image/"):
            print(f"  Media skipped (content type {content_type}): {url[:80]}")
            return None
        key = f"{prefix.strip('/')}/{hashlib.sha256(data).hexdigest()}{_guess_ext(url, content_type)}"
        if not bucket.object_exists(key):
            bucket.put_object(
                key,
                data,
                headers={
                    "Content-Type": content_type or "application/octet-stream",
                    "Cache-Control": "max-age=31536000, immutable",
                },
            )
        return {
            "url": f"https://{OSS_CUSTOM_DOMAIN}/{key}",
            "key": key,
            "content_type": content_type or None,
            "bytes": len(data),
        }
    except Exception as e:
        print(f"  Media archive failed: {url[:80]} | {e}")
        return None


MAX_VIDEO_BYTES = 50 * 1024 * 1024
VIDEO_DOWNLOAD_TIMEOUT = (CONNECT_TIMEOUT, 120)


def media_url(key: str) -> str:
    return f"https://{OSS_CUSTOM_DOMAIN}/{key}"


def archive_video(url: str, key: str, max_bytes: int = MAX_VIDEO_BYTES) -> dict[str, object] | None:
    """Keep the mp4 at `url` on OSS under `key` (named by the caller, e.g. by media_key).

    An object already at `key` is not downloaded again, so the hourly runs'
    overlapping windows cost nothing. The download is streamed to a temporary
    file and stopped as soon as it passes max_bytes.

    Returns {"url", "key", "bytes"} when stored, {"too_large": True, "bytes"}
    when the video is over the cap (the caller keeps the cover only), or None
    when OSS is not configured or the download/upload failed.
    """
    bucket = _get_bucket()
    if bucket is None:
        return None
    try:
        if bucket.object_exists(key):
            return {"url": media_url(key), "key": key, "bytes": None}
        import tempfile

        resp = http_get(
            url,
            timeout=VIDEO_DOWNLOAD_TIMEOUT,
            stream=True,
            retries=2,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MediaFetcher/1.0)"},
        )
        try:
            if resp.status_code != 200:
                print(f"  Video download failed [{resp.status_code}]: {url[:80]}")
                return None
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type and not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                print(f"  Video skipped (content type {content_type}): {url[:80]}")
                return None
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                return {"too_large": True, "bytes": declared}
            size = 0
            with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        return {"too_large": True, "bytes": size}
                    tmp.write(chunk)
                tmp.flush()
                if size < 1000:
                    print(f"  Video skipped (size {size} bytes): {url[:80]}")
                    return None
                bucket.put_object_from_file(
                    key,
                    tmp.name,
                    headers={"Content-Type": "video/mp4", "Cache-Control": "max-age=31536000, immutable"},
                )
            return {"url": media_url(key), "key": key, "bytes": size}
        finally:
            resp.close()
    except Exception as e:
        print(f"  Video archive failed: {url[:80]} | {e}")
        return None


def upload_images_to_oss(urls: list[str], date_str: str | None = None) -> list[str]:
    """Upload a list of image URLs to OSS.

    Returns a list of the same length; each element is either the OSS URL
    or the original URL if upload failed.
    """
    if not _get_bucket():
        return list(urls)

    if not date_str:
        date_str = date.today().strftime("%Y%m%d")

    result = []
    for url in urls:
        oss_url = upload_image_to_oss(url, date_str)
        result.append(oss_url or url)
    return result
