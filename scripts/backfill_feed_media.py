"""One-off maintenance for the 动态 feed's raw_items (Feed Media Backfill workflow).

1. Videos: rows of the last --days whose videos/GIFs only have a cover are
   looked up again on twitterapi.io (by tweet id, 50 per request) and their
   media is archived afresh, mp4 included (crawler.sources.twitter_feed).
2. X URLs: every feed row (any date) loses its X CDN URLs
   (media[].source_url, extra.avatar_source_url); media that never reached
   OSS is dropped.

Only rows that change are written. --dry-run reports what would change and
writes nothing; --inspect prints the media JSON of one looked-up tweet, to
check the API's field names.

    python -m scripts.backfill_feed_media --days 3 [--dry-run] [--inspect]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        return None

from crawler.sources.twitter_feed import VIDEO_TYPES, MediaStats, archive_post_media, media_source, strip_x_sources
from crawler.sources.twitterapi_io import TwitterApiClient, media_of, require_api_key, tweets_by_ids
from infra.dao import OctopusDao

SUB_SOURCE_TYPE = "x_feed_accounts"
ALL_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)


def needs_video(row: dict) -> bool:
    media = (row.get("context_content") or {}).get("media") or []
    return any(
        isinstance(m, dict) and m.get("type") in VIDEO_TYPES and not m.get("video_url") and not m.get("video_too_large")
        for m in media
    )


def tweet_id(row: dict) -> str:
    extra = row.get("extra") or {}
    return str(extra.get("tweet_id") or str(row.get("url") or "").rsplit("/", 1)[-1])


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Archive feed videos and strip X URLs from raw_items")
    parser.add_argument("--days", type=int, default=3, help="Look up videos of rows published in the last N days")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="Print one looked-up tweet's media JSON")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    with OctopusDao.from_env() as dao:
        rows = dao.raw_items.select_published_since([SUB_SOURCE_TYPE], ALL_TIME)
        print(f"feed rows: {len(rows)}")

        recent = [r for r in rows if r["source_published_date"] >= since and needs_video(r)]
        print(f"rows of the last {args.days} days with a video still to archive: {len(recent)}")
        stats = MediaStats()
        refetched = 0
        if recent:
            with TwitterApiClient(require_api_key()) as client:
                found = tweets_by_ids(client, [tweet_id(r) for r in recent])
                requests = client.requests
            print(f"looked up {len(recent)} tweets in {requests} request(s), found {len(found)}")
            if args.inspect and found:
                sample = next((t for t in found.values() if t.get("extendedEntities") or t.get("extended_entities")), None)
                if sample:
                    container = sample.get("extendedEntities") or sample.get("extended_entities")
                    print("sample media JSON:", json.dumps(container, ensure_ascii=False)[:3000])
            for row in recent:
                raw = found.get(tweet_id(row))
                if raw is None:
                    continue  # deleted or protected: keep the covers we have
                if args.dry_run:
                    refs = media_of(raw)
                    print(f"  {row['url']}: {[(m.type, bool(m.video_url)) for m in refs]}")
                    continue
                row["context_content"]["media"] = archive_post_media([media_source(m) for m in media_of(raw)], stats)
                row["_changed"] = True
                refetched += 1
            print(f"videos: {stats}")

        changed = 0
        for row in rows:
            context = row.get("context_content") or {}
            extra = row.get("extra") or {}
            if strip_x_sources(context, extra) or row.pop("_changed", False):
                changed += 1
                if not args.dry_run:
                    dao.raw_items.update_context_and_extra(row["id"], context, extra)
        verb = "would update" if args.dry_run else "updated"
        print(f"{verb} {changed} rows ({refetched} with re-archived media)")


if __name__ == "__main__":
    main()
