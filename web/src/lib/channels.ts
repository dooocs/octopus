import type { ScraperChannel } from '../types'

export const scraperChannels: ScraperChannel[] = [
  {
    type: 'github_trending',
    label: 'GitHub Trending',
    group: 'Code',
    sourceType: 'code_host',
    itemType: 'repo',
    description: '抓取 GitHub Trending 仓库榜单。',
    defaultInput: {
      timeout: 15
    },
    hint: '{ timeout }'
  },
  {
    type: 'github_search',
    label: 'GitHub Search',
    group: 'Code',
    sourceType: 'code_host',
    itemType: 'repo',
    description: '按关键词搜索近期 GitHub 仓库。',
    defaultInput: {
      queries: [{ q: 'ai agents stars:>100', label: 'ai_agents' }],
      per_page: 30,
      fetch_window_days: 7
    },
    hint: '{ queries:[{ q, label }], per_page, fetch_window_days }'
  },
  {
    type: 'hackernews',
    label: 'HackerNews',
    group: 'Community',
    sourceType: 'community',
    itemType: 'article',
    description: '抓取 Hacker News 高分新帖。',
    defaultInput: {
      new_n: 100,
      min_score: 50,
      cutoff_hours: 36,
      fetch_workers: 5,
      skip_domains: ['twitter.com', 'x.com', 'medium.com', 'zhihu.com']
    },
    hint: '{ new_n, min_score, cutoff_hours, fetch_workers, skip_domains }'
  },
  {
    type: 'rss',
    label: 'RSS Feed',
    group: 'Website',
    sourceType: 'website',
    itemType: 'article',
    description: '抓取标准 RSS/Atom 订阅源。',
    defaultInput: {
      url: '',
      max_items: 10,
      fetch_window_hours: 25
    },
    hint: '{ url, max_items, fetch_window_hours }'
  },
  {
    type: 'twitter_twscrape',
    label: 'Twitter / X',
    group: 'Social',
    sourceType: 'social',
    itemType: 'post',
    description: '抓取关注账号和关键词命中的推文。',
    defaultInput: {
      watch_accounts: [],
      tracked_keywords: [],
      max_age_days: 2,
      timeline_min_faves: 50
    },
    hint: '{ watch_accounts, tracked_keywords, max_age_days, timeline_min_faves }'
  },
  {
    type: 'twitter_nitter',
    label: 'Twitter / Nitter',
    group: 'Social',
    sourceType: 'social',
    itemType: 'post',
    description: '抓取指定 Twitter/X 账号的帖子。',
    defaultInput: {
      twitter_user: '',
      nitter_instances: ['https://nitter.poast.org'],
      aggregate: true,
      fetch_window_hours: 25
    },
    hint: '{ twitter_user, nitter_instances, aggregate, fetch_window_hours }'
  },
  {
    type: 'ai_blog',
    label: 'AI Blog',
    group: 'Website',
    sourceType: 'website',
    itemType: 'article',
    description: '抓取 AI 公司新闻页或博客页面。',
    defaultInput: {
      base_url: '',
      news_url: '',
      link_selector: "a[href*='/news/']",
      fetch_window_hours: 0
    },
    hint: '{ base_url, news_url, link_selector, fetch_window_hours }'
  },
  {
    type: 'community_v2ex',
    label: 'V2EX',
    group: 'Community',
    sourceType: 'community',
    itemType: 'discussion',
    description: '抓取 V2EX 热门技术讨论。',
    defaultInput: {
      top_n: 10,
      max_replies_to_fetch: 30,
      max_replies_to_keep: 15
    },
    hint: '{ top_n, max_replies_to_fetch, max_replies_to_keep }'
  },
  {
    type: 'community_linuxdo',
    label: 'LinuxDo',
    group: 'Community',
    sourceType: 'community',
    itemType: 'discussion',
    description: '抓取 LinuxDo 热门讨论。',
    defaultInput: {
      top_n: 10,
      max_replies_to_fetch: 30
    },
    hint: '{ top_n, max_replies_to_fetch }'
  },
  {
    type: 'reddit',
    label: 'Reddit',
    group: 'Community',
    sourceType: 'community',
    itemType: 'discussion',
    description: '抓取指定 subreddit 的高分讨论。',
    defaultInput: {
      subreddit: 'LocalLLaMA',
      min_score: 50,
      skip_nsfw: true,
      skip_stickied: true,
      skip_discussion_below: 100,
      skip_self_text_below: 200
    },
    hint: '{ subreddit, min_score, skip_nsfw, skip_stickied }'
  },
  {
    type: 'hf_model',
    label: 'Hugging Face Models',
    group: 'AI',
    sourceType: 'model_hub',
    itemType: 'model',
    description: '抓取 Hugging Face 模型趋势。',
    defaultInput: {
      min_likes: 50,
      min_downloads: 1000,
      limit: 3,
      max_retries: 3
    },
    hint: '{ min_likes, min_downloads, limit, max_retries }'
  },
  {
    type: 'hf_papers',
    label: 'Hugging Face Papers',
    group: 'AI',
    sourceType: 'model_hub',
    itemType: 'paper',
    description: '抓取 Hugging Face Daily Papers。',
    defaultInput: {
      top_n: 3,
      max_retries: 3
    },
    hint: '{ top_n, max_retries }'
  },
  {
    type: 'product_hunt',
    label: 'Product Hunt',
    group: 'Product',
    sourceType: 'product',
    itemType: 'product',
    description: '抓取 Product Hunt 高票新产品。',
    defaultInput: {
      min_votes: 200,
      max_retries: 3,
      topic_whitelist: [],
      topic_blacklist: []
    },
    hint: '{ min_votes, topic_whitelist, topic_blacklist, max_retries }'
  }
]

export function getChannel(type: string) {
  return scraperChannels.find((channel) => channel.type === type)
}
