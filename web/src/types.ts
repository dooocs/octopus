export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue }

export type ScraperConfigRow = {
  id: string | number
  name: string
  scraper_type: string
  enabled: boolean
  priority: number
  config: Record<string, JsonValue>
  created_at?: string | null
  updated_at?: string | null
}

export type ScraperConfigDraft = {
  id?: string | number
  name: string
  scraper_type: string
  enabled: boolean
  priority: number
  config: Record<string, JsonValue>
}

export type ScraperChannel = {
  type: string
  label: string
  group: string
  sourceType: string
  itemType: string
  description: string
  defaultConfig: Record<string, JsonValue>
  hint: string
}

export type AuthUser = {
  id: string
  email?: string
  name: string
  login: string
  avatarUrl: string
}
