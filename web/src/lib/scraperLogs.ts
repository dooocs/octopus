import type { ScraperLogDateSummaryRow, ScraperLogRow } from '../types'
import { supabase } from './supabase'

const LOG_TABLE_NAME = 'octp_scraper_logs'

export async function listScraperLogDateSummaries() {
  if (!supabase) return []

  const { data, error } = await supabase
    .from(LOG_TABLE_NAME)
    .select('snapshot_date, status, created_date')
    .order('snapshot_date', { ascending: false })
    .order('created_date', { ascending: false })
    .limit(1000)

  if (error) throw error
  return (data || []) as ScraperLogDateSummaryRow[]
}

export async function listScraperLogsByDate(snapshotDate: string) {
  if (!supabase || !snapshotDate) return []

  const { data, error } = await supabase
    .from(LOG_TABLE_NAME)
    .select('*')
    .eq('snapshot_date', snapshotDate)
    .order('created_date', { ascending: false })
    .limit(200)

  if (error) throw error
  return (data || []) as ScraperLogRow[]
}
