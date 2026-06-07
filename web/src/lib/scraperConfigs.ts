import type { ItemTypeRow, ScraperConfigDraft, ScraperConfigRow } from '../types'
import { supabase } from './supabase'

const CONFIG_TABLE_NAME = 'octp_scraper_configs'
const ITEM_TYPE_TABLE_NAME = 'octp_item_types'

export async function listScraperConfigs() {
  if (!supabase) return []

  const { data, error } = await supabase
    .from(CONFIG_TABLE_NAME)
    .select('*')
    .order('priority', { ascending: true })
    .order('name', { ascending: true })

  if (error) throw error
  return (data || []) as ScraperConfigRow[]
}

export async function listItemTypes() {
  if (!supabase) return []

  const { data, error } = await supabase
    .from(ITEM_TYPE_TABLE_NAME)
    .select('item_type, name, description')
    .order('item_type', { ascending: true })

  if (error) throw error
  return (data || []) as ItemTypeRow[]
}

export async function saveScraperConfig(draft: ScraperConfigDraft) {
  if (!supabase) return

  const payload = {
    name: draft.name,
    scraper: draft.scraper,
    enabled: draft.enabled,
    priority: draft.priority,
    source_type: draft.source_type,
    sub_source_type: draft.sub_source_type,
    item_type: draft.item_type,
    input: draft.input
  }

  if (draft.id === undefined) {
    const { error } = await supabase.from(CONFIG_TABLE_NAME).insert([payload])
    if (error) throw error
    return
  }

  const { error } = await supabase
    .from(CONFIG_TABLE_NAME)
    .update(payload)
    .eq('id', draft.id)

  if (error) throw error
}

export async function setScraperConfigEnabled(id: string | number, enabled: boolean) {
  if (!supabase) return

  const { error } = await supabase
    .from(CONFIG_TABLE_NAME)
    .update({ enabled })
    .eq('id', id)

  if (error) throw error
}

export async function deleteScraperConfig(id: string | number) {
  if (!supabase) return

  const { error } = await supabase.from(CONFIG_TABLE_NAME).delete().eq('id', id)
  if (error) throw error
}
