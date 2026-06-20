# scrapers/product_hunt.py

import os
import time
import requests
from datetime import datetime, timezone, timedelta
from infra.http import http_post
from infra.models import BaseScraper, RawItem
from scrapers.registry import register

PH_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

# PT = UTC-8（不处理 DST，PH 用 PT 而非 PDT）
PT_OFFSET = timedelta(hours=-8)

# 默认 AI 相关 topic 白名单
DEFAULT_TOPIC_WHITELIST = [
    "artificial-intelligence",
    "developer-tools",
    "productivity",
    "chatbots",
    "no-code",
    "open-source",
    "machine-learning",
]

# 默认跳过的 topic（crypto 噪音）
DEFAULT_TOPIC_BLACKLIST = [
    "crypto",
    "web3",
    "nft",
    "blockchain",
    "defi",
    "dao",
    "token",
]

GRAPHQL_QUERY = """
query Posts($postedAfter: DateTime!, $postedBefore: DateTime!) {
    posts(postedAfter: $postedAfter, postedBefore: $postedBefore, order: VOTES, first: 50) {
        edges {
            node {
                id
                name
                tagline
                description
                url
                website
                votesCount
                commentsCount
                createdAt
                topics(first: 10) { edges { node { name slug } } }
                makers { name username }
            }
        }
    }
}
"""

GRAPHQL_QUERY_FALLBACK = """
query Posts($postedAfter: DateTime!, $postedBefore: DateTime!) {
    posts(postedAfter: $postedAfter, postedBefore: $postedBefore, order: VOTES, first: 50) {
        edges {
            node {
                id
                name
                tagline
                description
                url
                website
                votesCount
                commentsCount
                createdAt
                topics(first: 10) { edges { node { name slug } } }
                makers { name username }
            }
        }
    }
}
"""

GRAPHQL_COMMENTS_QUERY = """
query PostComments($id: ID!) {
    post(id: $id) {
        comments(first: 10) {
            edges {
                node {
                    id
                    body
                    createdAt
                    user { name username }
                }
            }
        }
    }
}
"""


def _retry_post(url: str, json_body: dict, headers: dict, max_retries: int = 3) -> requests.Response:
    """指数退避重试：1s / 3s / 9s"""
    for attempt in range(max_retries):
        try:
            resp = http_post(url, json=json_body, headers=headers, timeout=20)
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < max_retries - 1:
                    wait = 3 ** attempt
                    print(f"  ⚠️ HTTP {resp.status_code}，{wait}s 后重试 ({attempt + 1}/{max_retries})")
                    time.sleep(wait)
                    continue
            return resp
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 3 ** attempt
                print(f"  ⚠️ 超时，{wait}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            raise
    return resp


def _pt_day_range(days_ago: int) -> tuple[str, str]:
    """计算 PT 时区 N 天前的起止 ISO 时间字符串。"""
    now_utc = datetime.now(timezone.utc)
    now_pt = now_utc + PT_OFFSET
    target_pt = (now_pt - timedelta(days=days_ago)).date()
    start_pt = datetime(target_pt.year, target_pt.month, target_pt.day, tzinfo=timezone.utc) - PT_OFFSET
    end_pt = start_pt + timedelta(days=1)
    return start_pt.isoformat(), end_pt.isoformat()


def _comment_nodes(node: dict) -> list[dict]:
    comments = []
    edges = node.get("comments", {}).get("edges", []) if isinstance(node.get("comments"), dict) else []
    for edge in edges:
        comment = edge.get("node", {}) if isinstance(edge, dict) else {}
        body = (comment.get("body") or "").strip()
        if not body:
            continue
        user = comment.get("user") or {}
        comments.append(
            {
                "id": comment.get("id"),
                "author": user.get("username") or user.get("name") or "",
                "author_name": user.get("name") or "",
                "text": body[:1000],
                "created_at": comment.get("createdAt"),
            }
        )
    return comments


def _comments_from_payload(payload: dict) -> list[dict]:
    data = payload.get("data") or {}
    for key in ("post", "node"):
        node = data.get(key)
        if isinstance(node, dict):
            return _comment_nodes(node)
    edges = ((data.get("posts") or {}).get("edges") or []) if isinstance(data.get("posts"), dict) else []
    if edges:
        node = edges[0].get("node", {}) if isinstance(edges[0], dict) else {}
        if isinstance(node, dict):
            return _comment_nodes(node)
    return []


def _enrich_enabled(config: dict, name: str, default: bool = True) -> bool:
    enrich = config.get("enrich")
    if not isinstance(enrich, list):
        return default
    return any(isinstance(item, dict) and item.get("name") == name for item in enrich)


@register("product_hunt")
class ProductHuntEngine(BaseScraper):
    def fetch(self) -> list[RawItem]:
        return self.enrich_items(self.discover_items())

    def discover_items(self) -> list[RawItem]:
        # API token：优先 config，其次环境变量
        api_token = self.config.get("api_token") or os.environ.get("PRODUCTHUNT_TOKEN", "")
        if not api_token:
            print("  ❌ Product Hunt: 未配置 api_token 且环境变量 PRODUCTHUNT_TOKEN 为空")
            return []

        source_type = self.config.get("source_type", "PRODUCT")
        content_type = self.config.get("content_type", "product_hunt")
        min_votes = self.config.get("min_votes", 200)
        max_retries = self.config.get("max_retries", 3)
        topic_whitelist = self.config.get("topic_whitelist", DEFAULT_TOPIC_WHITELIST)
        topic_blacklist = self.config.get("topic_blacklist", DEFAULT_TOPIC_BLACKLIST)
        t0 = time.time()

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        fetched = 0
        skipped = 0
        errors = 0
        items = []

        # 昨天的帖子可能还没积累投票，回退到前天
        edges = []
        for days_ago in [1, 2]:
            posted_after, posted_before = _pt_day_range(days_ago)
            body = {
                "query": GRAPHQL_QUERY,
                "variables": {"postedAfter": posted_after, "postedBefore": posted_before},
            }

            try:
                resp = _retry_post(PH_GRAPHQL_URL, body, headers, max_retries=max_retries)
                if resp.status_code != 200:
                    print(f"  ❌ Product Hunt 返回 HTTP {resp.status_code}: {resp.text[:200]}")
                    return []

                data = resp.json()
                if data.get("errors") and not data.get("data", {}).get("posts"):
                    fallback_body = {
                        "query": GRAPHQL_QUERY_FALLBACK,
                        "variables": {"postedAfter": posted_after, "postedBefore": posted_before},
                    }
                    resp = _retry_post(PH_GRAPHQL_URL, fallback_body, headers, max_retries=max_retries)
                    if resp.status_code != 200:
                        print(f"  ❌ Product Hunt fallback 返回 HTTP {resp.status_code}: {resp.text[:200]}")
                        return []
                    data = resp.json()
                edges = data.get("data", {}).get("posts", {}).get("edges", [])
            except Exception as e:
                print(f"  ❌ Product Hunt 请求失败: {e}")
                return []

            # 检查是否有投票数据，没有则回退
            if edges:
                top_votes = edges[0].get("node", {}).get("votesCount", 0)
                if top_votes > 0:
                    break
                print(f"  ⚠️ PH {days_ago}天前帖子投票为 0，尝试更早日期")

        for edge in edges:
            node = edge.get("node", {})
            fetched += 1

            try:
                votes = node.get("votesCount", 0)

                # 过滤：投票阈值
                if votes < min_votes:
                    skipped += 1
                    continue

                # 提取 topics
                topic_edges = node.get("topics", {}).get("edges", [])
                topic_slugs = [te["node"]["slug"] for te in topic_edges if te.get("node", {}).get("slug")]
                topic_names = [te["node"]["name"] for te in topic_edges if te.get("node", {}).get("name")]

                # 过滤：全部是 crypto 噪音
                if topic_slugs and all(s in topic_blacklist for s in topic_slugs):
                    skipped += 1
                    continue

                # 过滤：至少一个 topic 在白名单中
                if topic_whitelist and not any(s in topic_whitelist for s in topic_slugs):
                    skipped += 1
                    continue

                # 字段映射
                name = node.get("name", "").strip()
                if not name:
                    skipped += 1
                    continue

                tagline = node.get("tagline", "")
                description = node.get("description", "")
                summary = f"{tagline} · {description}" if description else tagline
                summary = summary[:500]

                ph_url = node.get("url", "")
                if not ph_url:
                    skipped += 1
                    continue

                website = node.get("website", "")

                # makers
                makers = node.get("makers", [])
                maker_names = [m.get("name", "") for m in makers if m.get("name")]
                author = ", ".join(maker_names[:3])
                if len(maker_names) > 3:
                    author += f" +{len(maker_names) - 3}"

                # published_at
                created_at = node.get("createdAt", "")
                published_at = None
                if created_at:
                    try:
                        published_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                item = RawItem(
                    title=name,
                    original_url=ph_url,
                    source_name=self.name,
                    source_type=source_type,
                    content_type=content_type,
                    author=author,
                    body_text=summary,
                    raw_metrics={"votes": votes, "comments": node.get("commentsCount", 0)},
                    extra={
                        "ph_id": node.get("id", ""),
                        "topics": topic_names,
                        "topic_slugs": topic_slugs,
                        "makers": [{"name": m.get("name", ""), "username": m.get("username", "")} for m in makers],
                        "website": website,
                        "tagline": tagline,
                        "source_tag": "product_hunt",
                    },
                    context_content={
                        "top_comments": [],
                    },
                    published_at=published_at,
                )
                items.append(item)
            except Exception as e:
                errors += 1
                print(f"  ⚠️ 解析 PH post 失败: {e}")

        duration_ms = int((time.time() - t0) * 1000)
        print(f"  [{self.name}] fetched={fetched} new={len(items)} skipped={skipped} errors={errors} duration={duration_ms}ms")
        return items

    def enrich_items(self, items: list[RawItem]) -> list[RawItem]:
        if not _enrich_enabled(self.config, "product_comments"):
            return items
        api_token = self.config.get("api_token") or os.environ.get("PRODUCTHUNT_TOKEN", "")
        if not api_token:
            return items
        max_retries = self.config.get("max_retries", 3)
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        for item in items:
            ph_id = item.extra.get("ph_id")
            if not ph_id:
                continue
            try:
                resp = _retry_post(
                    PH_GRAPHQL_URL,
                    {"query": GRAPHQL_COMMENTS_QUERY, "variables": {"id": ph_id}},
                    headers,
                    max_retries=max_retries,
                )
                if resp.status_code != 200:
                    continue
                comments = _comments_from_payload(resp.json())
                item.context_content["top_comments"] = comments
                item.context_content["top_comments_basis"] = "product_hunt_graphql_order"
            except Exception:
                continue
        return items
