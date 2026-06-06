# scrapers/twitter_twscrape.py
#
# Twitter 抓取引擎 — 通过 TwitterAPI.io 第三方 API 获取推文。
# 对外接口（@register、fetch 签名、RawItem 输出）与旧 twscrape 版本完全兼容。

import os
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from infra.models import BaseScraper, RawItem
from scrapers.registry import register

# 并发控制：避免同时太多请求触发限流
_SEM = asyncio.Semaphore(5)


# ---------------------------------------------------------------------------
# 内部数据结构 — 隔离第三方 schema，切换 provider 时只改 _from_api_response
# ---------------------------------------------------------------------------

@dataclass
class _Tweet:
    id: str
    url: str
    author: str            # username
    author_id: str
    author_name: str       # display name
    author_verified: bool
    author_followers: int
    text: str
    likes: int
    retweets: int
    replies: int
    quotes: int
    views: int             # 缺失统一为 0
    is_reply: bool
    lang: str
    created_at: datetime   # tz-aware UTC


# ---------------------------------------------------------------------------
# 异常分类
# ---------------------------------------------------------------------------

class _RetryableError(Exception):
    pass


class _AuthError(Exception):
    pass


# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------

def _parse_twitter_date(s: str) -> datetime:
    """处理 Twitter 经典日期格式 'Wed Oct 10 20:19:24 +0000 2018'。"""
    return parsedate_to_datetime(s)


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

@register("twitter_twscrape")
class TwitterTwscrapeEngine(BaseScraper):
    BASE_URL = "https://api.twitterapi.io"

    def fetch(self) -> list[RawItem]:
        try:
            return asyncio.run(self._fetch_all())
        except KeyboardInterrupt:
            raise
        except Exception as e:
            # SIGALRM 产生的 _TimeoutError 是 BaseException 的子类，
            # 但 Python 3 中 signal 只能在主线程使用，asyncio.run 会把它包装成
            # 普通异常。这里直接 re-raise 让 scrape.py 的 _TimeoutError 处理块捕获。
            if "超时" in str(e):
                raise
            print(f"⚠️ TwitterScraper 失败: {e}")
            return []

    # ---- 主入口 ----

    async def _fetch_all(self) -> list[RawItem]:
        api_key = os.environ.get("TWITTERAPI_IO_KEY")
        if not api_key:
            print("  ⚠️ TWITTERAPI_IO_KEY 未设置，跳过")
            return []

        max_age = self.config.get("max_age_days", 2)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)
        seen: set[str] = set()
        lock = asyncio.Lock()
        items: list[RawItem] = []

        async with httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"X-API-Key": api_key},
            timeout=30.0,
        ) as client:
            self._client = client

            # ---- 单个关键词搜索 ----
            async def _search_keyword(kw: str) -> int:
                async with _SEM:
                    query = f'"{kw}" -is:retweet lang:en min_faves:100'
                    try:
                        tweets = await self._paginated_fetch(
                            "/twitter/tweet/advanced_search",
                            {"query": query, "queryType": "Latest"},
                            cutoff,
                        )
                        new_count = 0
                        for tw in tweets:
                            item = self._to_raw_item(tw, cutoff, seen)
                            if item:
                                async with lock:
                                    items.append(item)
                                new_count += 1
                        print(f"  [搜索] {kw} → {new_count} 条")
                        return new_count
                    except Exception as e:
                        print(f"  [搜索] 失败 ({kw}): {e}")
                        return 0

            # ---- 单个账号时间线 ----
            async def _fetch_account(username: str) -> int:
                async with _SEM:
                    min_faves = self.config.get("timeline_min_faves", 50)
                    try:
                        tweets = await self._paginated_fetch(
                            "/twitter/user/last_tweets",
                            {"userName": username},
                            cutoff,
                        )
                        new_count = 0
                        for tw in tweets:
                            if tw.likes < min_faves:
                                continue
                            item = self._to_raw_item(tw, cutoff, seen)
                            if item:
                                async with lock:
                                    items.append(item)
                                new_count += 1
                        if new_count:
                            print(f"  [@{username}] → {new_count} 条")
                        return new_count
                    except Exception as e:
                        print(f"  [@{username}] 失败: {e}")
                        return 0

            # 并发执行关键词搜索
            keywords = self.config.get("tracked_keywords", [])
            print(f"  开始关键词搜索，共 {len(keywords)} 个关键词（并发 5）")
            await asyncio.gather(*[_search_keyword(kw) for kw in keywords])

            # 并发执行账号时间线
            accounts = self.config.get("watch_accounts", [])
            print(f"  开始账号时间线抓取，共 {len(accounts)} 个账号（并发 5）")
            await asyncio.gather(*[_fetch_account(u) for u in accounts])

        print(f"  共抓取 {len(items)} 条（去重后）")
        return items

    # ---- 分页拉取 ----

    async def _paginated_fetch(
        self,
        endpoint: str,
        params: dict,
        cutoff: datetime,
        max_pages: int = 5,
    ) -> list[_Tweet]:
        """循环拉取分页，遇到老于 cutoff 的推文或空页就停止。"""
        all_tweets: list[_Tweet] = []
        cursor = None
        for _ in range(max_pages):
            page_params = {**params}
            if cursor:
                page_params["cursor"] = cursor
            resp = await self._request_with_retry(endpoint, page_params)
            # 响应结构: {"data": {"tweets": [...]}, "has_next_page": bool, "next_cursor": str}
            inner = resp.get("data") or {}
            tweets = [self._from_api_response(t) for t in inner.get("tweets", [])]
            # 空页直接停止（API 可能返回 has_next_page=true 但实际无数据）
            if not tweets:
                break
            all_tweets.extend(tweets)
            # 早停：本页最旧的推文已老于 cutoff
            if tweets[-1].created_at < cutoff:
                break
            if not resp.get("has_next_page"):
                break
            cursor = resp.get("next_cursor")
            if not cursor:
                break
        return all_tweets

    # ---- HTTP 请求 + 重试 ----

    @retry(
        retry=retry_if_exception_type(_RetryableError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _request_with_retry(self, endpoint: str, params: dict) -> dict:
        resp = await self._client.get(endpoint, params=params)
        if resp.status_code in (401, 403):
            raise _AuthError(f"auth failed: {resp.text[:200]}")
        if resp.status_code == 429:
            raise _RetryableError("rate limited")
        if 500 <= resp.status_code < 600:
            raise _RetryableError(f"server error {resp.status_code}")
        if resp.status_code != 200:
            raise RuntimeError(f"unexpected {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # ---- 字段映射 ----

    @staticmethod
    def _from_api_response(raw: dict) -> _Tweet:
        """TwitterAPI.io 响应 → _Tweet。切换 provider 时只改这一个方法。"""
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

    # ---- RawItem 转换 ----

    def _to_raw_item(self, tw: _Tweet, cutoff: datetime, seen: set) -> RawItem | None:
        if tw.url in seen or tw.created_at < cutoff:
            return None
        if tw.is_reply:
            return None
        seen.add(tw.url)
        return RawItem(
            title=tw.text[:100],
            original_url=tw.url,
            source_name=self.name,
            source_type=self.config.get("source_type", "TWEET"),
            content_type=self.config.get("content_type", "tweet"),
            author=tw.author,
            author_url=f"https://x.com/{tw.author}",
            body_text=tw.text,
            raw_metrics={
                "likes": tw.likes,
                "retweets": tw.retweets,
                "replies": tw.replies,
                "views": tw.views,
            },
            extra={
                "tweet_id": tw.id,
                "display_name": tw.author_name,
                "verified": tw.author_verified,
                "followers": tw.author_followers,
            },
            published_at=tw.created_at,
        )
