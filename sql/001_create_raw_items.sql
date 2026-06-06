-- Octopus final output table for Aliyun RDS MySQL 8.0.
-- This table stores successful crawler output items only.

CREATE TABLE IF NOT EXISTS raw_items (
  id CHAR(32) NOT NULL COMMENT '全局唯一 ID',
  url TEXT NOT NULL COMMENT '内容本身 URL',

  source_type VARCHAR(64) NOT NULL COMMENT '来源大类，如 website/social/community/code_host',
  sub_source_type VARCHAR(128) NOT NULL COMMENT '具体来源或采集通道，如 github_trending/openai_blog_rss',
  item_type VARCHAR(64) NOT NULL COMMENT '内容对象类型，如 article/repo/post/tweet/paper/product',
  author_id VARCHAR(255) NULL COMMENT '来源侧作者稳定 ID',
  author_url TEXT NULL COMMENT '作者主页 URL',

  created_date DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '八爪鱼首次写入时间，UTC',
  updated_date DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
    ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '八爪鱼最后更新时间，UTC',
  source_published_date DATETIME(3) NULL COMMENT '来源发布时间，UTC',
  snapshot_date DATE NOT NULL COMMENT '业务归属日期',

  title TEXT NOT NULL COMMENT '标题',
  metrics JSON NULL COMMENT '来源原生指标，如 stars/likes/comments/views',
  content MEDIUMTEXT NULL COMMENT '主内容，下游默认消费文本',
  context_content JSON NULL COMMENT '主内容之外的结构化上下文，如摘要/评论/thread/引用/README',
  extra JSON NULL COMMENT '来源特定扩展字段',
  scrape_config_snapshot JSON NULL COMMENT '抓取配置快照',

  PRIMARY KEY (id),
  KEY idx_snapshot_date (snapshot_date),
  KEY idx_source_snapshot (source_type, sub_source_type, snapshot_date),
  KEY idx_item_type_snapshot (item_type, snapshot_date),
  KEY idx_source_published_date (source_published_date),
  KEY idx_author_id (author_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
