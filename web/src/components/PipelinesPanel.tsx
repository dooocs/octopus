import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, GitBranch, Loader2, RefreshCw } from 'lucide-react'
import type { JsonValue, PipelineRow, PipelineRunRow, ScraperConfigRow } from '../types'
import { listPipelineRuns, setPipelineEnabled } from '../lib/pipelines'
import ToggleSwitch from './ToggleSwitch'

type PipelinesPanelProps = {
  pipelines: PipelineRow[]
  configs: ScraperConfigRow[]
  loading: boolean
  onReload: () => void
  onChanged: (pipeline: PipelineRow) => void
}

const statusLabels: Record<PipelineRunRow['status'], string> = {
  running: '运行中',
  success: '成功',
  partial: '部分成功',
  failed: '失败'
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date)
}

function duration(run: PipelineRunRow) {
  if (!run.finished_at) return '-'
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  return Number.isFinite(ms) ? `${Math.max(0, Math.round(ms / 1000))}s` : '-'
}

function asList(value: JsonValue | undefined): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : []
}

function asObject(value: JsonValue | undefined): Record<string, JsonValue> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

/** The run summary fields worth a glance; the rest stays in the database. */
function summaryText(summary: Record<string, JsonValue>) {
  const parts: string[] = []
  const pick = (key: string, label: string) => {
    if (typeof summary[key] === 'number') parts.push(`${label} ${summary[key]}`)
  }
  pick('posts', '帖子')
  pick('translated', '新翻译')
  pick('translations_reused', '复用')
  pick('digests_generated', '新摘要')
  const llm = asObject(summary.llm)
  if (typeof llm.prompt_tokens === 'number') {
    parts.push(`tokens ${llm.prompt_tokens}+${typeof llm.completion_tokens === 'number' ? llm.completion_tokens : 0}`)
  }
  return parts.join(' · ') || '-'
}

function PipelineRuns({ pipelineId }: { pipelineId: string }) {
  const [runs, setRuns] = useState<PipelineRunRow[] | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      setRuns(await listPipelineRuns(pipelineId))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    }
  }, [pipelineId])

  useEffect(() => {
    void load()
  }, [load])

  if (error) return <p className="pipeline-runs-empty">运行记录读取失败：{error}</p>
  if (runs === null) return <p className="pipeline-runs-empty">读取运行记录...</p>
  if (runs.length === 0) return <p className="pipeline-runs-empty">还没有运行记录。</p>

  return (
    <table className="pipeline-runs">
      <thead>
        <tr>
          <th>开始</th>
          <th>状态</th>
          <th>耗时</th>
          <th>结果</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.id} className={`run-${run.status}`}>
            <td>{formatTime(run.started_at)}</td>
            <td>{statusLabels[run.status] || run.status}</td>
            <td>{duration(run)}</td>
            <td title={run.error_message || undefined}>
              {run.status === 'failed' ? run.error_message || '失败' : summaryText(run.summary || {})}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function PipelinesPanel({ pipelines, configs, loading, onReload, onChanged }: PipelinesPanelProps) {
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const configName = (id: string) => configs.find((row) => row.id === id)?.name || `已归档的配置 ${id.slice(0, 8)}`

  async function toggle(pipeline: PipelineRow, enabled: boolean) {
    setBusyId(pipeline.id)
    setError('')
    try {
      await setPipelineEnabled(pipeline.id, enabled)
      onChanged({ ...pipeline, enabled })
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : String(toggleError))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="effective-panel" aria-labelledby="pipelines-title">
      <div className="section-head">
        <div>
          <div className="eyebrow">Processing</div>
          <h2 id="pipelines-title">加工流程</h2>
        </div>
        <button className="icon-button" type="button" aria-label="刷新加工流程" onClick={onReload}>
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </div>
      <p className="channel-desc">
        加工流程读取 raw_items，写入自己的产出表。"读取"是按条件选数据（软依赖），删掉一个博主或一条配置只会让输入变少；
        "依赖的采集配置"是硬依赖，被依赖的配置不能删除或归档，只能停用。
      </p>

      {error ? (
        <div className="notice notice-error">
          <AlertCircle size={18} aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}

      {loading ? (
        <div className="empty-inline">
          <Loader2 size={20} className="spin" aria-hidden="true" />
          <span>读取加工流程...</span>
        </div>
      ) : pipelines.length === 0 ? (
        <div className="empty-inline">
          <GitBranch size={22} aria-hidden="true" />
          <span>还没有加工流程。</span>
        </div>
      ) : (
        <div className="pipeline-list">
          {pipelines.map((pipeline) => {
            const selector = asObject(pipeline.input.selector)
            const llm = asObject(pipeline.input.llm)
            const sources = pipeline.octp_pipeline_sources || []
            return (
              <article className={`pipeline-card ${pipeline.enabled ? '' : 'disabled'}`} key={pipeline.id}>
                <header>
                  <div>
                    <strong>{pipeline.name}</strong>
                    <span>{pipeline.slug} · {pipeline.kind} · 更新于 {formatTime(pipeline.updated_date)}</span>
                  </div>
                  <ToggleSwitch
                    checked={pipeline.enabled}
                    disabled={busyId === pipeline.id}
                    label={`切换 ${pipeline.name} 启用状态`}
                    onChange={(enabled) => void toggle(pipeline, enabled)}
                  />
                </header>
                <dl className="pipeline-facts">
                  <dt>读取</dt>
                  <dd>
                    {asList(selector.sub_source_types).map((value) => <code key={value}>{value}</code>)}
                    {typeof selector.window_hours === 'number' ? <span>最近 {selector.window_hours} 小时</span> : null}
                  </dd>
                  <dt>依赖的采集配置</dt>
                  <dd>
                    {sources.length === 0 ? <span>无</span> : sources.map((s) => <span key={s.scraper_config_id}>{configName(s.scraper_config_id)}</span>)}
                  </dd>
                  <dt>模型</dt>
                  <dd>
                    <code>{String(llm.model || '-')}</code>
                    {pipeline.input.prompt_version !== undefined ? <span>prompt v{String(pipeline.input.prompt_version)}</span> : null}
                  </dd>
                </dl>
                <PipelineRuns pipelineId={pipeline.id} />
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
