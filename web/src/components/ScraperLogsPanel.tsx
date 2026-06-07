import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  CalendarDays,
  CheckCircle2,
  Clock3,
  ExternalLink,
  FileWarning,
  Loader2,
  PauseCircle,
  RefreshCw,
  Terminal,
  XCircle
} from 'lucide-react'
import type {
  JsonValue,
  ScraperConfigRow,
  ScraperLogDateSummaryRow,
  ScraperLogRow,
  ScraperLogStatus
} from '../types'
import { listScraperLogDateSummaries, listScraperLogsByDate } from '../lib/scraperLogs'

type DateSummary = {
  date: string
  total: number
  success: number
  failed: number
  running: number
  partial: number
  skipped: number
  latestCreated: string
}

type ScraperLogsPanelProps = {
  configs: ScraperConfigRow[]
}

const statusLabels: Record<ScraperLogStatus, string> = {
  running: '运行中',
  success: '成功',
  failed: '失败',
  partial: '部分成功',
  skipped: '跳过'
}

function groupLogDateRows(rows: ScraperLogDateSummaryRow[]) {
  const summaries = new Map<string, DateSummary>()

  for (const row of rows) {
    if (!row.snapshot_date) continue

    const current =
      summaries.get(row.snapshot_date) ||
      ({
        date: row.snapshot_date,
        total: 0,
        success: 0,
        failed: 0,
        running: 0,
        partial: 0,
        skipped: 0,
        latestCreated: ''
      } satisfies DateSummary)

    current.total += 1
    current[row.status] += 1
    if (row.created_date && (!current.latestCreated || row.created_date > current.latestCreated)) {
      current.latestCreated = row.created_date
    }
    summaries.set(row.snapshot_date, current)
  }

  return Array.from(summaries.values()).sort((left, right) => right.date.localeCompare(left.date))
}

function formatDate(value?: string | null) {
  if (!value) return '-'
  const [year, month, day] = value.split('-')
  if (!year || !month || !day) return value
  return `${year}-${month}-${day}`
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'

  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}

function formatDuration(durationMs?: number | null) {
  if (durationMs === undefined || durationMs === null) return '-'
  if (durationMs < 1000) return `${durationMs} ms`
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} s`
  return `${Math.round(durationMs / 60_000)} min`
}

function readText(record: Record<string, JsonValue> | undefined, key: string) {
  const value = record?.[key]
  return typeof value === 'string' ? value : ''
}

function readNumber(record: Record<string, JsonValue> | undefined, key: string) {
  const value = record?.[key]
  return typeof value === 'number' ? value : null
}

function formatJson(value: JsonValue | JsonValue[] | Record<string, JsonValue>) {
  return JSON.stringify(value, null, 2)
}

function getStatusIcon(status: ScraperLogStatus) {
  if (status === 'success') return <CheckCircle2 size={18} aria-hidden="true" />
  if (status === 'failed') return <XCircle size={18} aria-hidden="true" />
  if (status === 'partial') return <AlertTriangle size={18} aria-hidden="true" />
  if (status === 'skipped') return <PauseCircle size={18} aria-hidden="true" />
  return <Loader2 size={18} className="spin" aria-hidden="true" />
}

function summarizeDate(summary: DateSummary) {
  const parts = []
  if (summary.success) parts.push(`${summary.success} 成功`)
  if (summary.failed) parts.push(`${summary.failed} 失败`)
  if (summary.running) parts.push(`${summary.running} 运行中`)
  if (summary.partial) parts.push(`${summary.partial} 部分成功`)
  if (summary.skipped) parts.push(`${summary.skipped} 跳过`)
  return parts.join(' · ') || `${summary.total} 次`
}

export default function ScraperLogsPanel({ configs }: ScraperLogsPanelProps) {
  const [dateRows, setDateRows] = useState<ScraperLogDateSummaryRow[]>([])
  const [selectedDate, setSelectedDate] = useState('')
  const [logs, setLogs] = useState<ScraperLogRow[]>([])
  const [loadingDates, setLoadingDates] = useState(true)
  const [loadingLogs, setLoadingLogs] = useState(false)
  const [error, setError] = useState('')

  const configsById = useMemo(() => {
    const items = new Map<string, ScraperConfigRow>()
    for (const config of configs) {
      items.set(config.id, config)
    }
    return items
  }, [configs])

  const dateSummaries = useMemo(() => groupLogDateRows(dateRows), [dateRows])
  const selectedSummary = dateSummaries.find((summary) => summary.date === selectedDate)

  const loadDates = useCallback(async () => {
    setLoadingDates(true)
    setError('')
    try {
      const rows = await listScraperLogDateSummaries()
      const summaries = groupLogDateRows(rows)
      setDateRows(rows)
      setSelectedDate((current) => {
        if (current && summaries.some((summary) => summary.date === current)) return current
        return summaries[0]?.date || ''
      })
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setLoadingDates(false)
    }
  }, [])

  useEffect(() => {
    void loadDates()
  }, [loadDates])

  useEffect(() => {
    let cancelled = false

    async function loadLogsForDate() {
      if (!selectedDate) {
        setLogs([])
        return
      }

      setLoadingLogs(true)
      setError('')
      try {
        const rows = await listScraperLogsByDate(selectedDate)
        if (!cancelled) setLogs(rows)
      } catch (loadError) {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : String(loadError))
      } finally {
        if (!cancelled) setLoadingLogs(false)
      }
    }

    void loadLogsForDate()
    return () => {
      cancelled = true
    }
  }, [selectedDate])

  async function refreshLogs() {
    await loadDates()
    if (selectedDate) {
      setLoadingLogs(true)
      try {
        const rows = await listScraperLogsByDate(selectedDate)
        setLogs(rows)
      } catch (loadError) {
        setError(loadError instanceof Error ? loadError.message : String(loadError))
      } finally {
        setLoadingLogs(false)
      }
    }
  }

  function logTitle(row: ScraperLogRow) {
    const config = row.scraper_config_id ? configsById.get(row.scraper_config_id) : undefined
    return readText(row.config_snapshot, 'name') || config?.name || '未命名抓取器'
  }

  function logScraper(row: ScraperLogRow) {
    const config = row.scraper_config_id ? configsById.get(row.scraper_config_id) : undefined
    return readText(row.config_snapshot, 'scraper') || config?.scraper || 'unknown'
  }

  return (
    <section className="logs-panel" aria-labelledby="logs-title">
      <div className="section-head">
        <div>
          <div className="eyebrow">Scraper Logs</div>
          <h2 id="logs-title">运行日志</h2>
        </div>
        <button className="secondary-button" type="button" onClick={() => void refreshLogs()}>
          <RefreshCw size={16} aria-hidden="true" />
          <span>刷新</span>
        </button>
      </div>

      {error ? (
        <div className="notice notice-error logs-error">
          <AlertCircle size={18} aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="logs-layout">
        <div className="logs-list" aria-live="polite">
          {selectedSummary ? (
            <div className="logs-summary">
              <div>
                <CalendarDays size={17} aria-hidden="true" />
                <strong>{formatDate(selectedSummary.date)}</strong>
              </div>
              <span>{summarizeDate(selectedSummary)}</span>
            </div>
          ) : null}

          {loadingLogs ? (
            <div className="empty-state">
              <RefreshCw className="spin" size={26} aria-hidden="true" />
              <span>加载日志中...</span>
            </div>
          ) : logs.length === 0 ? (
            <div className="empty-state">
              <FileWarning size={28} aria-hidden="true" />
              <span>{loadingDates ? '读取日期中...' : '没有日志记录。'}</span>
            </div>
          ) : (
            logs.map((row) => {
              const result = row.result || {}
              const runUrl = readText(result, 'github_run_url')
              const itemCount = readNumber(result, 'items_count')
              const affectedCount = readNumber(result, 'raw_items_affected')

              return (
                <article className={`log-row log-row-${row.status}`} key={row.id}>
                  <div className={`log-status-icon status-${row.status}`}>
                    {getStatusIcon(row.status)}
                  </div>
                  <div className="log-main">
                    <div className="log-title">
                      <strong>{logTitle(row)}</strong>
                      <span className={`status-pill status-${row.status}`}>{statusLabels[row.status]}</span>
                    </div>
                    <div className="log-meta">
                      <span>{logScraper(row)}</span>
                      <span>{formatTime(row.created_date)}</span>
                      <span>
                        <Clock3 size={12} aria-hidden="true" />
                        {formatDuration(row.duration_ms)}
                      </span>
                      {itemCount !== null ? <span>{itemCount} items</span> : null}
                      {affectedCount !== null ? <span>{affectedCount} affected</span> : null}
                      {row.github_run_id ? <span>run {row.github_run_id}</span> : null}
                    </div>
                    {row.error_message ? <div className="log-error-message">{row.error_message}</div> : null}
                    <details className="log-details">
                      <summary>
                        <Terminal size={14} aria-hidden="true" />
                        <span>执行详情</span>
                      </summary>
                      <pre>
                        {formatJson({
                          config_snapshot: row.config_snapshot,
                          result: row.result,
                          error_logs: row.error_logs
                        })}
                      </pre>
                    </details>
                  </div>
                  {runUrl ? (
                    <a
                      className="icon-button log-run-link"
                      href={runUrl}
                      aria-label="打开 GitHub Actions 运行记录"
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink size={16} aria-hidden="true" />
                    </a>
                  ) : null}
                </article>
              )
            })
          )}
        </div>

        <aside className="date-rail" aria-label="日志日期">
          <div className="date-rail-head">
            <CalendarDays size={16} aria-hidden="true" />
            <strong>日期</strong>
          </div>
          <div className="date-list">
            {loadingDates ? (
              <div className="date-loading">
                <Loader2 size={18} className="spin" aria-hidden="true" />
              </div>
            ) : dateSummaries.length === 0 ? (
              <div className="date-empty">暂无日期</div>
            ) : (
              dateSummaries.map((summary) => (
                <button
                  className={`date-item ${selectedDate === summary.date ? 'active' : ''}`}
                  key={summary.date}
                  type="button"
                  onClick={() => setSelectedDate(summary.date)}
                >
                  <span>{formatDate(summary.date)}</span>
                  <small>{summary.total} 次</small>
                  <em>{summarizeDate(summary)}</em>
                </button>
              ))
            )}
          </div>
        </aside>
      </div>
    </section>
  )
}
