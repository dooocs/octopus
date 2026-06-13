import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  CalendarDays,
  Database,
  ExternalLink,
  FileText,
  RefreshCw,
  Search,
  Tag,
  X
} from 'lucide-react'
import type { JsonValue, RawItemSnapshotRow } from '../types'
import { listRawItemSnapshotRows } from '../lib/rawItemsSnapshot'

type ItemTypeSummary = {
  itemType: string
  count: number
}

const ALL_TYPES = '__all__'

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

function previewText(value?: string | null, maxLength = 180) {
  const text = (value || '').replace(/\s+/g, ' ').trim()
  if (!text) return '无正文内容'
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength)}...`
}

function formatJson(value: JsonValue | null | undefined) {
  if (value === undefined || value === null) return '-'
  return JSON.stringify(value, null, 2)
}

function itemMatchesQuery(row: RawItemSnapshotRow, query: string) {
  if (!query) return true
  const haystack = [
    row.title,
    row.url,
    row.author_id,
    row.source_type,
    row.sub_source_type,
    row.item_type,
    row.content
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()

  return haystack.includes(query)
}

function summarizeItemTypes(rows: RawItemSnapshotRow[]) {
  const summaries = new Map<string, ItemTypeSummary>()
  for (const row of rows) {
    const itemType = row.item_type || 'unknown'
    const current = summaries.get(itemType) || { itemType, count: 0 }
    current.count += 1
    summaries.set(itemType, current)
  }

  return Array.from(summaries.values()).sort((left, right) => {
    if (right.count !== left.count) return right.count - left.count
    return left.itemType.localeCompare(right.itemType)
  })
}

function snapshotDateLabel(rows: RawItemSnapshotRow[]) {
  const dates = Array.from(new Set(rows.map((row) => row.snapshot_date).filter(Boolean))).sort().reverse()
  if (dates.length === 0) return '-'
  if (dates.length === 1) return formatDate(dates[0])
  return `${formatDate(dates[0])} +${dates.length - 1}`
}

export default function RawItemsSnapshotPanel() {
  const [rows, setRows] = useState<RawItemSnapshotRow[]>([])
  const [selectedItemType, setSelectedItemType] = useState(ALL_TYPES)
  const [selectedRow, setSelectedRow] = useState<RawItemSnapshotRow | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadRows = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await listRawItemSnapshotRows()
      setRows(data)
      setSelectedItemType((current) => {
        if (current === ALL_TYPES) return current
        return data.some((row) => row.item_type === current) ? current : ALL_TYPES
      })
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadRows()
  }, [loadRows])

  const itemTypeSummaries = useMemo(() => summarizeItemTypes(rows), [rows])
  const normalizedQuery = query.trim().toLowerCase()

  const visibleRows = useMemo(() => {
    return rows
      .filter((row) => selectedItemType === ALL_TYPES || row.item_type === selectedItemType)
      .filter((row) => itemMatchesQuery(row, normalizedQuery))
      .sort((left, right) => {
        const sourceCompare = left.sub_source_type.localeCompare(right.sub_source_type)
        if (sourceCompare !== 0) return sourceCompare
        return (right.updated_date || '').localeCompare(left.updated_date || '')
      })
  }, [normalizedQuery, rows, selectedItemType])

  const selectedTypeCount =
    selectedItemType === ALL_TYPES
      ? rows.length
      : itemTypeSummaries.find((summary) => summary.itemType === selectedItemType)?.count || 0

  return (
    <section className="snapshot-panel" aria-labelledby="snapshot-title">
      <div className="section-head">
        <div>
          <div className="eyebrow">Raw Items Snapshot</div>
          <h2 id="snapshot-title">快照内容</h2>
        </div>
        <button className="secondary-button" type="button" onClick={() => void loadRows()}>
          <RefreshCw size={16} aria-hidden="true" />
          <span>刷新</span>
        </button>
      </div>

      {error ? (
        <div className="notice notice-error snapshot-error">
          <AlertCircle size={18} aria-hidden="true" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="snapshot-toolbar">
        <div className="search-box snapshot-search">
          <Search size={16} aria-hidden="true" />
          <input
            value={query}
            placeholder="搜索标题、URL、来源、作者或正文"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className="snapshot-summary-strip" aria-label="快照统计">
          <div>
            <span>{rows.length}</span>
            <small>总条目</small>
          </div>
          <div>
            <span>{selectedTypeCount}</span>
            <small>当前分类</small>
          </div>
          <div>
            <span>{snapshotDateLabel(rows)}</span>
            <small>快照日期</small>
          </div>
        </div>
      </div>

      <div className="snapshot-layout">
        <div className="snapshot-list" aria-live="polite">
          {loading ? (
            <div className="empty-state">
              <RefreshCw className="spin" size={26} aria-hidden="true" />
              <span>加载快照内容中...</span>
            </div>
          ) : visibleRows.length === 0 ? (
            <div className="empty-state">
              <FileText size={28} aria-hidden="true" />
              <span>{rows.length === 0 ? '暂无快照内容。' : '没有匹配的快照内容。'}</span>
            </div>
          ) : (
            visibleRows.map((row) => (
              <button
                className="snapshot-row"
                key={row.id}
                type="button"
                onClick={() => setSelectedRow(row)}
              >
                <div className="snapshot-row-icon">
                  <FileText size={18} aria-hidden="true" />
                </div>
                <div className="snapshot-row-main">
                  <div className="snapshot-row-title">
                    <strong>{row.title}</strong>
                    <span>{row.item_type || 'unknown'}</span>
                  </div>
                  <p>{previewText(row.content)}</p>
                  <div className="snapshot-row-meta">
                    <span>{row.source_type}</span>
                    <span>{row.sub_source_type}</span>
                    <span>{formatDate(row.snapshot_date)}</span>
                    {row.source_published_date ? <span>发布 {formatTime(row.source_published_date)}</span> : null}
                    <span>{formatTime(row.updated_date)}</span>
                    {row.author_id ? <span>{row.author_id}</span> : null}
                  </div>
                </div>
              </button>
            ))
          )}
        </div>

        <aside className="snapshot-rail" aria-label="快照分类">
          <div className="date-rail-head">
            <Tag size={16} aria-hidden="true" />
            <strong>分类</strong>
          </div>
          <div className="snapshot-type-list">
            <button
              className={`snapshot-type-item ${selectedItemType === ALL_TYPES ? 'active' : ''}`}
              type="button"
              onClick={() => setSelectedItemType(ALL_TYPES)}
            >
              <span>全部</span>
              <small>{rows.length} 条</small>
            </button>
            {itemTypeSummaries.map((summary) => (
              <button
                className={`snapshot-type-item ${selectedItemType === summary.itemType ? 'active' : ''}`}
                key={summary.itemType}
                type="button"
                onClick={() => setSelectedItemType(summary.itemType)}
              >
                <span>{summary.itemType}</span>
                <small>{summary.count} 条</small>
              </button>
            ))}
          </div>
        </aside>
      </div>

      {selectedRow ? (
        <div className="modal-layer" role="presentation" onClick={() => setSelectedRow(null)}>
          <article
            className="snapshot-detail-modal"
            aria-labelledby="snapshot-detail-title"
            role="dialog"
            aria-modal="true"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div>
                <div className="eyebrow">Raw Item</div>
                <h2 id="snapshot-detail-title">{selectedRow.title}</h2>
              </div>
              <button className="icon-button" type="button" aria-label="关闭快照详情" onClick={() => setSelectedRow(null)}>
                <X size={17} aria-hidden="true" />
              </button>
            </header>

            <div className="snapshot-detail-body">
              <div className="snapshot-detail-meta">
                <span>{selectedRow.item_type}</span>
                <span>{selectedRow.source_type}</span>
                <span>{selectedRow.sub_source_type}</span>
                <span>快照 {formatDate(selectedRow.snapshot_date)}</span>
                <span>创建 {formatTime(selectedRow.created_date)}</span>
                <span>更新 {formatTime(selectedRow.updated_date)}</span>
                {selectedRow.source_published_date ? (
                  <span>发布 {formatTime(selectedRow.source_published_date)}</span>
                ) : null}
              </div>

              <div className="snapshot-detail-link">
                <Database size={15} aria-hidden="true" />
                <code>{selectedRow.id}</code>
              </div>

              <a className="snapshot-url" href={selectedRow.url} target="_blank" rel="noreferrer">
                <ExternalLink size={15} aria-hidden="true" />
                <span>{selectedRow.url}</span>
              </a>

              {selectedRow.author_id || selectedRow.author_url ? (
                <div className="snapshot-author">
                  <strong>{selectedRow.author_id || 'unknown author'}</strong>
                  {selectedRow.author_url ? (
                    <a href={selectedRow.author_url} target="_blank" rel="noreferrer">
                      {selectedRow.author_url}
                    </a>
                  ) : null}
                </div>
              ) : null}

              <section className="snapshot-content-block">
                <h3>正文</h3>
                <p>{selectedRow.content || '无正文内容'}</p>
              </section>

              <div className="snapshot-json-grid">
                <details open>
                  <summary>metrics</summary>
                  <pre>{formatJson(selectedRow.metrics)}</pre>
                </details>
                <details>
                  <summary>context_content</summary>
                  <pre>{formatJson(selectedRow.context_content)}</pre>
                </details>
                <details>
                  <summary>extra</summary>
                  <pre>{formatJson(selectedRow.extra)}</pre>
                </details>
                <details>
                  <summary>scrape_config_snapshot</summary>
                  <pre>{formatJson(selectedRow.scrape_config_snapshot)}</pre>
                </details>
              </div>
            </div>
          </article>
        </div>
      ) : null}
    </section>
  )
}
