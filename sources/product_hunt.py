from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta

import requests

from core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord, enrich_enabled
from core.registry import register_adapter
from infra.http import http_post

from .spec_helpers import INTEGER, STRING, default_input, input_schema

PH_GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"
PT_OFFSET = timedelta(hours=-8)

DEFAULT_TOPIC_WHITELIST = [
    "artificial-intelligence",
    "developer-tools",
    "productivity",
    "chatbots",
    "no-code",
    "open-source",
    "machine-learning",
]

DEFAULT_TOPIC_BLACKLIST = ["crypto", "web3", "nft", "blockchain", "defi", "dao", "token"]

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
    for attempt in range(max_retries):
        try:
            response = http_post(url, json=json_body, headers=headers, timeout=20)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(3**attempt)
                continue
            return response
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(3**attempt)
                continue
            raise
    return response


def _pt_day_range(days_ago: int) -> tuple[str, str]:
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


@register_adapter
class ProductHuntAdapter(SourceAdapterBase):
    spec = ChannelSpec(
        scraper="product_hunt",
        label="Product Hunt",
        group="Product",
        default_source_type="product_platform",
        default_item_type="product",
        input_schema_version=1,
        input_schema=input_schema(
            source={"api_token": STRING},
            fetch={"max_retries": INTEGER},
            filters={
                "min_votes": INTEGER,
                "topic_whitelist": {"type": "array", "items": STRING},
                "topic_blacklist": {"type": "array", "items": STRING},
            },
            enrich_names=["product_comments"],
        ),
        default_input=default_input(
            filters={"min_votes": 200, "topic_whitelist": [], "topic_blacklist": []},
            fetch={"max_retries": 3},
            enrich=[{"name": "product_comments", "when": "always"}],
        ),
        required_secrets=["PRODUCTHUNT_TOKEN"],
        supported_enrichers=["product_comments"],
        description="抓取 Product Hunt 高票新产品。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        api_token = self._api_token(config)
        if not api_token:
            return []
        filters = config.input.filters
        min_votes = int(filters.get("min_votes") or 200)
        max_retries = int(config.input.fetch.get("max_retries") or 3)
        topic_whitelist = filters.get("topic_whitelist") or DEFAULT_TOPIC_WHITELIST
        topic_blacklist = filters.get("topic_blacklist") or DEFAULT_TOPIC_BLACKLIST
        headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

        edges = []
        for days_ago in [1, 2]:
            posted_after, posted_before = _pt_day_range(days_ago)
            body = {"query": GRAPHQL_QUERY, "variables": {"postedAfter": posted_after, "postedBefore": posted_before}}
            resp = _retry_post(PH_GRAPHQL_URL, body, headers, max_retries=max_retries)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if data.get("errors") and not data.get("data", {}).get("posts"):
                fallback_body = {
                    "query": GRAPHQL_QUERY_FALLBACK,
                    "variables": {"postedAfter": posted_after, "postedBefore": posted_before},
                }
                resp = _retry_post(PH_GRAPHQL_URL, fallback_body, headers, max_retries=max_retries)
                if resp.status_code != 200:
                    return []
                data = resp.json()
            edges = data.get("data", {}).get("posts", {}).get("edges", [])
            if edges and edges[0].get("node", {}).get("votesCount", 0) > 0:
                break

        records: list[SourceRecord] = []
        for edge in edges:
            node = edge.get("node", {})
            votes = int(node.get("votesCount") or 0)
            if votes < min_votes:
                continue
            topic_edges = node.get("topics", {}).get("edges", [])
            topic_slugs = [item["node"]["slug"] for item in topic_edges if item.get("node", {}).get("slug")]
            topic_names = [item["node"]["name"] for item in topic_edges if item.get("node", {}).get("name")]
            if topic_slugs and all(slug in topic_blacklist for slug in topic_slugs):
                continue
            if topic_whitelist and not any(slug in topic_whitelist for slug in topic_slugs):
                continue
            name = str(node.get("name") or "").strip()
            ph_url = str(node.get("url") or "")
            if not name or not ph_url:
                continue
            tagline = node.get("tagline", "")
            description = node.get("description", "")
            summary = f"{tagline} · {description}" if description else tagline
            makers = node.get("makers", [])
            maker_names = [maker.get("name", "") for maker in makers if maker.get("name")]
            author = ", ".join(maker_names[:3])
            if len(maker_names) > 3:
                author += f" +{len(maker_names) - 3}"
            published_at = None
            if node.get("createdAt"):
                try:
                    published_at = datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            records.append(
                SourceRecord(
                    identity=f"producthunt:{node.get('id') or ph_url}",
                    url=ph_url,
                    title=name,
                    content=summary[:500],
                    metrics={"votes": votes, "comments": node.get("commentsCount", 0)},
                    extra={
                        "ph_id": node.get("id", ""),
                        "topics": topic_names,
                        "topic_slugs": topic_slugs,
                        "makers": [{"name": maker.get("name", ""), "username": maker.get("username", "")} for maker in makers],
                        "website": node.get("website", ""),
                        "tagline": tagline,
                        "source_tag": "product_hunt",
                    },
                    context_content={"top_comments": []},
                    author_id=author,
                    source_published_date=published_at,
                )
            )
        return records

    def enrich(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        if not enrich_enabled(config, "product_comments"):
            return records
        api_token = self._api_token(config)
        if not api_token:
            return records
        max_retries = int(config.input.fetch.get("max_retries") or 3)
        headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        for record in records:
            ph_id = record.extra.get("ph_id")
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
                record.context_content["top_comments"] = _comments_from_payload(resp.json())
                record.context_content["top_comments_basis"] = "product_hunt_graphql_order"
            except Exception:
                continue
        return records

    def _api_token(self, config: ScraperConfig) -> str:
        return str(config.input.source.get("api_token") or os.getenv("PRODUCTHUNT_TOKEN", ""))

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return RawItem(
            title=record.title,
            original_url=record.url,
            source_name=config.name,
            source_type=config.source_type,
            item_type=config.item_type,
            identity=record.identity,
            author=record.author_id,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
