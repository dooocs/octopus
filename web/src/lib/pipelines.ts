import type { PipelineReferences, PipelineRow, PipelineRunRow } from '../types'
import { supabase } from './supabase'

const PIPELINE_TABLE = 'octp_pipelines'
const RUN_TABLE = 'octp_pipeline_runs'

export async function listPipelines() {
  if (!supabase) return []

  const { data, error } = await supabase
    .from(PIPELINE_TABLE)
    .select('*, octp_pipeline_sources(scraper_config_id)')
    .order('name', { ascending: true })

  if (error) throw error
  return (data || []) as PipelineRow[]
}

export async function listPipelineRuns(pipelineId: string, limit = 8) {
  if (!supabase) return []

  const { data, error } = await supabase
    .from(RUN_TABLE)
    .select('*')
    .eq('pipeline_id', pipelineId)
    .order('started_at', { ascending: false })
    .limit(limit)

  if (error) throw error
  return (data || []) as PipelineRunRow[]
}

export async function setPipelineEnabled(id: string, enabled: boolean) {
  if (!supabase) return

  const { error } = await supabase.from(PIPELINE_TABLE).update({ enabled }).eq('id', id)
  if (error) throw error
}

/** Which pipelines hard-depend on each scraper config. */
export function pipelineReferences(pipelines: PipelineRow[]): PipelineReferences {
  const refs: PipelineReferences = new Map()
  for (const pipeline of pipelines) {
    for (const source of pipeline.octp_pipeline_sources || []) {
      const list = refs.get(source.scraper_config_id) || []
      list.push({ id: pipeline.id, name: pipeline.name })
      refs.set(source.scraper_config_id, list)
    }
  }
  return refs
}
