from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from crawler.core.contracts import ChannelSpec, RawItem, RunContext, ScraperConfig, SourceAdapterBase, SourceRecord
from crawler.core.registry import register_adapter

from .spec_helpers import INTEGER, STRING_ARRAY, default_input, input_schema
from .twitterapi_io import TwitterApiRetryableError, check_response, parse_created_at, require_api_key

_SEM = asyncio.Semaphore(5)


@dataclass
class _Tweet:
    id: str
    url: str
    author: str
    author_id: str
    author_name: str
    author_verified: bool
    author_followers: int
    text: str
    likes: int
    retweets: int
    replies: int
    quotes: int
    views: int
    is_reply: bool
    lang: str
    created_at: datetime


def _parse_twitter_date(value: str) -> datetime:
    return parse_created_at(value)


@register_adapter
class TwitterAdapter(SourceAdapterBase):
    BASE_URL = "https://api.twitterapi.io"
    spec = ChannelSpec(
        scraper="twitter_twscrape",
        label="Twitter / X",
        group="Social",
        default_source_type="social",
        default_item_type="post",
        input_schema_version=1,
        input_schema=input_schema(
            source={"watch_accounts": STRING_ARRAY, "tracked_keywords": STRING_ARRAY},
            fetch={"max_age_days": INTEGER},
            filters={"timeline_min_faves": INTEGER, "min_likes": INTEGER},
        ),
        default_input=default_input(
            source={"watch_accounts": [], "tracked_keywords": []},
            fetch={"max_age_days": 2},
            filters={"timeline_min_faves": 50},
        ),
        required_secrets=["TWITTERAPI_IO_KEY"],
        description="抓取关注账号和关键词命中的推文。",
    )

    def discover(self, ctx: RunContext, config: ScraperConfig) -> list[SourceRecord]:
        # Errors propagate: a missing key, an exhausted balance or an API error
        # fails the task instead of looking like "no new tweets".
        return asyncio.run(self._fetch_all(config))

    async def _fetch_all(self, config: ScraperConfig) -> list[SourceRecord]:
        api_key = require_api_key()
        max_age = int(config.input.fetch.get("max_age_days") or 2)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)
        seen: set[str] = set()
        lock = asyncio.Lock()
        records: list[SourceRecord] = []

        async with httpx.AsyncClient(base_url=self.BASE_URL, headers={"X-API-Key": api_key}, timeout=30.0) as client:
            self._client = client

            async def search_keyword(keyword: str) -> None:
                async with _SEM:
                    min_likes = int(config.input.filters.get("min_likes") or 100)
                    query = f'"{keyword}" -is:retweet lang:en min_faves:{min_likes}'
                    tweets = await self._paginated_fetch("/twitter/tweet/advanced_search", {"query": query, "queryType": "Latest"}, cutoff)
                    for tweet in tweets:
                        record = self._to_record(tweet, cutoff, seen, discover_via="keyword")
                        if record:
                            async with lock:
                                records.append(record)

            async def fetch_account(username: str) -> None:
                async with _SEM:
                    tweets = await self._paginated_fetch("/twitter/user/last_tweets", {"userName": username}, cutoff)
                    for tweet in tweets:
                        record = self._to_record(tweet, cutoff, seen, discover_via="account")
                        if record:
                            async with lock:
                                records.append(record)

            keywords = [str(item) for item in config.input.source.get("tracked_keywords", [])]
            accounts = [str(item) for item in config.input.source.get("watch_accounts", [])]
            await asyncio.gather(*[search_keyword(keyword) for keyword in keywords])
            await asyncio.gather(*[fetch_account(account) for account in accounts])
        return records

    def prune(self, ctx: RunContext, records: list[SourceRecord], config: ScraperConfig) -> list[SourceRecord]:
        min_likes = int(config.input.filters.get("min_likes") or 100)
        timeline_min_faves = int(config.input.filters.get("timeline_min_faves") or 50)
        pruned: list[SourceRecord] = []
        for record in records:
            likes = int(record.metrics.get("likes") or 0)
            threshold = min_likes if record.extra.get("discover_via") == "keyword" else timeline_min_faves
            if likes >= threshold:
                pruned.append(record)
        return pruned

    async def _paginated_fetch(self, endpoint: str, params: dict, cutoff: datetime, max_pages: int = 5) -> list[_Tweet]:
        all_tweets: list[_Tweet] = []
        cursor = None
        for _ in range(max_pages):
            page_params = {**params}
            if cursor:
                page_params["cursor"] = cursor
            resp = await self._request_with_retry(endpoint, page_params)
            inner = resp.get("data") or {}
            tweets = [self._from_api_response(item) for item in inner.get("tweets", [])]
            if not tweets:
                break
            all_tweets.extend(tweets)
            if tweets[-1].created_at < cutoff:
                break
            if not resp.get("has_next_page"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break
        return all_tweets

    @retry(
        retry=retry_if_exception_type((TwitterApiRetryableError, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _request_with_retry(self, endpoint: str, params: dict) -> dict:
        resp = await self._client.get(endpoint, params=params)
        try:
            body = resp.json()
        except ValueError:
            body = None
        return check_response(resp.status_code, body, resp.text)

    def _from_api_response(self, raw: dict) -> _Tweet:
        author = raw.get("author") or {}
        return _Tweet(
            id=str(raw["id"]),
            url=raw.get("url") or f"https://x.com/{author.get('userName', 'i')}/status/{raw['id']}",
            author=author.get("userName", ""),
            author_id=str(author.get("id", "")),
            author_name=author.get("name", ""),
            author_verified=bool(author.get("isBlueVerified", False)),
            author_followers=int(author.get("followers") or 0),
            text=raw.get("text", ""),
            likes=int(raw.get("likeCount") or 0),
            retweets=int(raw.get("retweetCount") or 0),
            replies=int(raw.get("replyCount") or 0),
            quotes=int(raw.get("quoteCount") or 0),
            views=int(raw.get("viewCount") or 0),
            is_reply=bool(raw.get("isReply", False)),
            lang=raw.get("lang", ""),
            created_at=_parse_twitter_date(raw["createdAt"]),
        )

    def _to_record(self, tweet: _Tweet, cutoff: datetime, seen: set[str], *, discover_via: str) -> SourceRecord | None:
        if tweet.url in seen or tweet.created_at < cutoff or tweet.is_reply:
            return None
        seen.add(tweet.url)
        return SourceRecord(
            identity=f"tweet:{tweet.id}",
            url=tweet.url,
            title=tweet.text[:100],
            content=tweet.text,
            metrics={"likes": tweet.likes, "retweets": tweet.retweets, "replies": tweet.replies, "views": tweet.views},
            extra={
                "tweet_id": tweet.id,
                "discover_via": discover_via,
                "display_name": tweet.author_name,
                "verified": tweet.author_verified,
                "followers": tweet.author_followers,
            },
            author_id=tweet.author,
            author_url=f"https://x.com/{tweet.author}",
            source_published_date=tweet.created_at,
        )

    def normalize(self, ctx: RunContext, record: SourceRecord, config: ScraperConfig) -> RawItem:
        return RawItem(
            title=record.title,
            original_url=record.url,
            source_name=config.name,
            source_type=config.source_type,
            item_type=config.item_type,
            identity=record.identity,
            author=record.author_id,
            author_url=record.author_url,
            body_text=record.content,
            raw_metrics=record.metrics,
            extra=record.extra,
            context_content=record.context_content,
            published_at=record.source_published_date,
        )
