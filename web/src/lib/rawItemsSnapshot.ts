import type { RawItemSnapshotRow } from '../types'
import { supabase } from './supabase'

const RAW_ITEMS_SNAPSHOT_TABLE_NAME = 'octp_snapshot_raw_items'

export async function listRawItemSnapshotRows() {
  if (!supabase) return []

  const { data, error } = await supabase
    .from(RAW_ITEMS_SNAPSHOT_TABLE_NAME)
    .select('*')
    .order('item_type', { ascending: true })
    .order('sub_source_type', { ascending: true })
    .order('updated_date', { ascending: false })
    .limit(1000)

  if (error) throw error
  return (data || []) as RawItemSnapshotRow[]
}
