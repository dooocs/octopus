export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export type ScraperConfigRow = {
  id: string
  name: string
  scraper: string
  enabled: boolean
  priority: number
  source_type: string
  sub_source_type: string
  item_type: string
  input: Record<string, JsonValue>
  created_date?: string | null
  updated_date?: string | null
}

export type ScraperConfigDraft = {
  id?: string
  name: string
  scraper: string
  enabled: boolean
  priority: number
  source_type: string
  sub_source_type: string
  item_type: string
  input: Record<string, JsonValue>
}

export type ScraperLogStatus = 'running' | 'success' | 'failed' | 'partial' | 'skipped'

export type ScraperLogRow = {
  id: string
  scraper_config_id?: string | null
  snapshot_date: string
  github_run_id?: string | null
  config_snapshot: Record<string, JsonValue>
  status: ScraperLogStatus
  duration_ms?: number | null
  result: Record<string, JsonValue>
  error_message?: string | null
  error_logs: JsonValue[]
  created_date?: string | null
  updated_date?: string | null
}

export type ScraperLogDateSummaryRow = {
  snapshot_date: string
  status: ScraperLogStatus
  created_date?: string | null
}

export type RawItemSnapshotRow = {
  id: string
  url: string
  source_type: string
  sub_source_type: string
  item_type: string
  author_id?: string | null
  author_url?: string | null
  created_date: string
  updated_date: string
  source_published_date?: string | null
  snapshot_date: string
  title: string
  metrics?: JsonValue | null
  content?: string | null
  context_content?: JsonValue | null
  extra?: JsonValue | null
  scrape_config_snapshot?: JsonValue | null
}

export type ItemTypeRow = {
  item_type: string
  name: string
  description?: string | null
}

export type ScraperChannel = {
  type: string
  label: string
  group: string
  sourceType: string
  itemType: string
  description: string
  defaultInput: Record<string, JsonValue>
  hint: string
}

export type AuthUser = {
  id: string
  email?: string
  name: string
  login: string
  avatarUrl: string
}
