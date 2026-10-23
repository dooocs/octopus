"""One-off: find 动态 blogger candidates by who the current seed accounts follow.

Pulls the following list of every seed account (twitterapi.io
/twitter/user/followings, 200 per page, $0.01 per 1k users), counts how many
seeds follow each account, then pulls one page of recent tweets for the top
candidates so posting frequency and original-post ratio can be judged.
Writes everything to the output JSON; nothing is written to any database.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

from crawler.sources.twitterapi_io import TwitterApiClient, TwitterApiError, parse_created_at, require_api_key

SEEDS = [
    "sama", "gdb", "karpathy", "AndrewYNg", "demishassabis", "ilyasut", "fchollet", "thom_wolf",
    "clem_delangue", "JeffDean", "drjimfan", "elonmusk", "simonw", "emollick", "ID_AA_Carmack",
    "levelsio", "marc_louvion", "dhh", "nearcyan", "goodside", "AravSrinivas", "paulg", "naval",
    "bcherny", "steipete", "tdinh_me", "dannypostmaa", "yongfook", "jackfriks", "HiTw93",
    "thepatwalls", "gregisenberg", "arvidkahl", "tibo_maker", "AlexHormozi", "lennysan",
    "jasonfried", "OfficialLoganK", "mckaywrigley", "alexalbert__", "shadcn", "Yangyixxxx", "Livid",
]
# Accounts already reviewed and dropped; still counted, flagged in the output.
DROPPED = ["tszzl", "dwarkesh_sp", "Rainmaker1973", "svpino", "ylecun", "realDonaldTrump", "OpenAI",
           "AnthropicAI", "GoogleDeepMind", "MistralAI", "huggingface", "perplexity_ai", "xai", "MetaAI"]

USER_KEYS = ("id", "name", "description", "followers_count", "following_count", "statuses_count",
             "created_at", "location", "verified", "isBlueVerified", "is_blue_verified")


def handle_of(user: dict) -> str:
    return str(user.get("screen_name") or user.get("userName") or "")


def followings(client: TwitterApiClient, seed: str, max_pages: int) -> list[dict]:
    out: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        body = client.get("/twitter/user/followings", {"userName": seed, "cursor": cursor, "pageSize": 200})
        out.extend(u for u in body.get("followings") or [] if isinstance(u, dict))
        cursor = body.get("next_cursor") or ""
        if not body.get("has_next_page") or not cursor:
            break
    return out


def recent_tweets(client: TwitterApiClient, handle: str) -> list[dict]:
    body = client.get("/twitter/user/last_tweets", {"userName": handle, "includeReplies": "false"})
    inner = body.get("data") if isinstance(body.get("data"), dict) else body
    tweets = []
    for raw in inner.get("tweets") or []:
        if not isinstance(raw, dict):
            continue
        is_rt = any(raw.get(k) for k in ("retweeted_tweet", "retweetedTweet", "retweeted_status"))
        is_quote = any(raw.get(k) for k in ("quoted_tweet", "quotedTweet", "quoted_status"))
        try:
            created = parse_created_at(str(raw.get("createdAt") or raw.get("created_at") or "")).isoformat()
        except Exception:
            created = None
        tweets.append({
            "created_at": created,
            "kind": "retweet" if is_rt else "quote" if is_quote else "reply" if raw.get("isReply") else "original",
            "text": str(raw.get("text") or "")[:280],
            "likes": raw.get("likeCount"),
            "lang": raw.get("lang"),
        })
    return tweets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="cofollow_result.json")
    ap.add_argument("--max-pages", type=int, default=25, help="pages of 200 per seed")
    ap.add_argument("--min-seeds", type=int, default=3, help="keep accounts followed by at least N seeds")
    ap.add_argument("--sample", type=int, default=120, help="pull recent tweets for the top N candidates")
    args = ap.parse_args()

    client = TwitterApiClient(require_api_key())
    followed_by: dict[str, list[str]] = defaultdict(list)
    profiles: dict[str, dict] = {}
    seed_stats: dict[str, object] = {}
    for seed in SEEDS:
        try:
            users = followings(client, seed, args.max_pages)
        except TwitterApiError as exc:
            seed_stats[seed] = f"error: {exc}"
            print(f"{seed}: {exc}", file=sys.stderr)
            continue
        seed_stats[seed] = len(users)
        print(f"{seed}: {len(users)} followings")
        for u in users:
            h = handle_of(u)
            if not h:
                continue
            key = h.lower()
            followed_by[key].append(seed)
            profiles.setdefault(key, {"handle": h, **{k: u.get(k) for k in USER_KEYS if k in u}})

    seeds_lower = {s.lower() for s in SEEDS}
    dropped_lower = {s.lower() for s in DROPPED}
    candidates = []
    for key, seeds in followed_by.items():
        if len(seeds) < args.min_seeds or key in seeds_lower:
            continue
        candidates.append({**profiles[key], "seed_count": len(seeds), "followed_by": sorted(seeds),
                           "previously_dropped": key in dropped_lower})
    candidates.sort(key=lambda c: (-c["seed_count"], -(c.get("followers_count") or 0)))

    for c in candidates[: args.sample]:
        if c["previously_dropped"]:
            continue
        try:
            c["recent"] = recent_tweets(client, c["handle"])
        except TwitterApiError as exc:
            c["recent_error"] = str(exc)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requests": client.requests,
        "seed_stats": seed_stats,
        "candidates": candidates,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"requests={client.requests} candidates={len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
