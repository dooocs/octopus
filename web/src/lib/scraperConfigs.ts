import type { ScraperConfigDraft, ScraperConfigRow } from '../types'
import { supabase } from './supabase'

const TABLE_NAME = 'scraper_configs'

export async function listScraperConfigs() {
  if (!supabase) return []

  const { data, error } = await supabase
    .from(TABLE_NAME)
    .select('*')
    .order('priority', { ascending: true })
    .order('name', { ascending: true })

  if (error) throw error
  return (data || []) as ScraperConfigRow[]
}

export async function saveScraperConfig(draft: ScraperConfigDraft) {
  if (!supabase) return

  const payload = {
    name: draft.name,
    scraper_type: draft.scraper_type,
    enabled: draft.enabled,
    priority: draft.priority,
    config: draft.config
  }

  if (draft.id === undefined) {
    const { error } = await supabase.from(TABLE_NAME).insert([payload])
    if (error) throw error
    return
  }

  const { error } = await supabase
    .from(TABLE_NAME)
    .update(payload)
    .eq('id', draft.id)

  if (error) throw error
}

export async function setScraperConfigEnabled(id: string | number, enabled: boolean) {
  if (!supabase) return

  const { error } = await supabase
    .from(TABLE_NAME)
    .update({ enabled })
    .eq('id', id)

  if (error) throw error
}

export async function deleteScraperConfig(id: string | number) {
  if (!supabase) return

  const { error } = await supabase.from(TABLE_NAME).delete().eq('id', id)
  if (error) throw error
}
