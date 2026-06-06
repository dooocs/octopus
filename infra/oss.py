# infra/oss.py
# Alibaba Cloud OSS image upload utility

from __future__ import annotations

import hashlib
import os
import time
from datetime import date
from urllib.parse import urlparse

import requests

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
        print(f"⚠️ OSS 初始化失败: {e}")
        _oss_enabled = False
        return None


def _guess_ext(url: str, content_type: str = "") -> str:
    if content_type:
        ext = _EXT_MAP.get(content_type.split(";")[0].strip().lower())
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
            resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ImageFetcher/1.0)",
            })
            if resp.status_code == 200:
                break
            if attempt < 2:
                print(f"  ⏳ 图片下载 {resp.status_code}，3s 后重试 ({attempt + 1}/2): {url[:60]}")
                time.sleep(3)
        if resp is None or resp.status_code != 200:
            print(f"  ⚠️ 图片下载失败 [{resp.status_code}]: {url[:80]}")
            return None

        content_type = resp.headers.get("Content-Type", "")
        image_data = resp.content

        if not image_data or len(image_data) < 100:
            return None

        ext = _guess_ext(url, content_type)
        oss_key = _build_oss_key(url, ext, date_str)

        bucket.put_object(oss_key, image_data, headers={
            "Content-Type": content_type or "application/octet-stream",
        })

        public_url = f"https://{OSS_CUSTOM_DOMAIN}/{oss_key}"

        return public_url

    except Exception as e:
        print(f"  ⚠️ OSS 上传失败: {url[:80]} | {e}")
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
