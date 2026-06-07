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
