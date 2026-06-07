import type { ScraperChannel } from '../types'

export const scraperChannels: ScraperChannel[] = [
  {
    type: 'github_trending',
    label: 'GitHub Trending',
    group: 'Code',
    sourceType: 'code_host',
    itemType: 'repo',
    description: '抓取 GitHub Trending 仓库榜单。',
    defaultConfig: {
      source_type: 'code_host',
      content_type: 'repo',
      timeout: 15
    },
    hint: '{ source_type, content_type, timeout }'
  },
  {
    type: 'github_search',
    label: 'GitHub Search',
    group: 'Code',
    sourceType: 'code_host',
    itemType: 'repo',
    description: '按关键词搜索近期 GitHub 仓库。',
    defaultConfig: {
      source_type: 'code_host',
      content_type: 'repo',
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
    defaultConfig: {
      source_type: 'community',
      content_type: 'article',
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
    defaultConfig: {
      source_type: 'website',
      content_type: 'article',
      url: '',
      max_items: 10,
      source_tag: '',
      fetch_window_hours: 25
    },
    hint: '{ url, max_items, source_tag, fetch_window_hours }'
  },
  {
    type: 'twitter_twscrape',
    label: 'Twitter / X',
    group: 'Social',
    sourceType: 'social',
    itemType: 'tweet',
    description: '抓取关注账号和关键词命中的推文。',
    defaultConfig: {
      source_type: 'social',
      content_type: 'tweet',
      watch_accounts: [],
      tracked_keywords: [],
      max_age_days: 2,
      timeline_min_faves: 50
    },
    hint: '{ watch_accounts, tracked_keywords, max_age_days, timeline_min_faves }'
  },
  {
    type: 'ai_blog',
    label: 'AI Blog',
    group: 'Website',
    sourceType: 'website',
    itemType: 'article',
    description: '抓取 AI 公司新闻页或博客页面。',
    defaultConfig: {
      source_type: 'website',
      content_type: 'article',
      base_url: '',
      news_url: '',
      link_selector: "a[href*='/news/']",
      source_tag: 'official_ai',
      fetch_window_hours: 0
    },
    hint: '{ base_url, news_url, link_selector, source_tag, fetch_window_hours }'
  },
  {
    type: 'community_v2ex',
    label: 'V2EX',
    group: 'Community',
    sourceType: 'community',
    itemType: 'post',
    description: '抓取 V2EX 热门技术讨论。',
    defaultConfig: {
      source_type: 'community',
      content_type: 'v2ex_hot',
      top_n: 10,
      max_replies_to_fetch: 30,
      max_replies_to_keep: 15,
      source_tag: 'dev_community'
    },
    hint: '{ top_n, max_replies_to_fetch, max_replies_to_keep, source_tag }'
  },
  {
    type: 'community_linuxdo',
    label: 'LinuxDo',
    group: 'Community',
    sourceType: 'community',
    itemType: 'post',
    description: '抓取 LinuxDo 热门讨论。',
    defaultConfig: {
      source_type: 'community',
      content_type: 'linuxdo_hot',
      top_n: 10,
      max_replies_to_fetch: 30,
      source_tag: 'dev_community'
    },
    hint: '{ top_n, max_replies_to_fetch, source_tag }'
  },
  {
    type: 'reddit',
    label: 'Reddit',
    group: 'Community',
    sourceType: 'community',
    itemType: 'post',
    description: '抓取指定 subreddit 的高分讨论。',
    defaultConfig: {
      source_type: 'community',
      content_type: 'reddit',
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
    type: 'huggingface',
    label: 'Hugging Face',
    group: 'AI',
    sourceType: 'model_hub',
    itemType: 'model',
    description: '抓取 Hugging Face 模型或论文趋势。',
    defaultConfig: {
      source_type: 'model_hub',
      content_type: 'hf_model',
      min_likes: 50,
      min_downloads: 1000,
      limit: 3,
      max_retries: 3
    },
    hint: '{ content_type, min_likes, min_downloads, limit, max_retries }'
  },
  {
    type: 'product_hunt',
    label: 'Product Hunt',
    group: 'Product',
    sourceType: 'product',
    itemType: 'product',
    description: '抓取 Product Hunt 高票新产品。',
    defaultConfig: {
      source_type: 'product',
      content_type: 'product_hunt',
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
