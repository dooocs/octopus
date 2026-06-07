import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Code2,
  Copy,
  Edit3,
  Github,
  LayoutList,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  XCircle
} from 'lucide-react'
import type { AuthUser, ItemTypeRow, ScraperChannel, ScraperConfigDraft, ScraperConfigRow } from '../types'
import { getChannel, scraperChannels } from '../lib/channels'
import {
  deleteScraperConfig,
  listItemTypes,
  listScraperConfigs,
  saveScraperConfig,
  setScraperConfigEnabled
} from '../lib/scraperConfigs'
import ConfigModal from './ConfigModal'
import ToggleSwitch from './ToggleSwitch'

type ScraperDashboardProps = {
  authUser: AuthUser
  onLogout: () => void
}

type ModalState =
  | { mode: 'create'; channel: ScraperChannel; row?: never }
  | { mode: 'edit'; channel: ScraperChannel; row: ScraperConfigRow }

type ActiveView = 'configs' | 'effective'

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

function rowToRuntimeConfig(row: ScraperConfigRow) {
  return {
    type: row.scraper,
    name: row.name,
    enabled: row.enabled,
    source_type: row.source_type,
    sub_source_type: row.sub_source_type,
    item_type: row.item_type,
    config: row.input
  }
}

function countEnabledByScraper(configs: ScraperConfigRow[], scraper: string, itemType: string) {
  return configs.filter(
    (config) => config.scraper === scraper && config.item_type === itemType && config.enabled
  ).length
}

function countByScraper(configs: ScraperConfigRow[], scraper: string, itemType: string) {
  return configs.filter((config) => config.scraper === scraper && config.item_type === itemType).length
}

function countEnabledByItemType(configs: ScraperConfigRow[], itemType: string) {
  return configs.filter((config) => config.item_type === itemType && config.enabled).length
}

function countByItemType(configs: ScraperConfigRow[], itemType: string) {
  return configs.filter((config) => config.item_type === itemType).length
}

function getDefaultCollapsedGroups() {
  return new Set(scraperChannels.map((channel) => channel.itemType))
}

function titleizeScraper(scraper: string) {
  return scraper
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

function channelFromConfig(row: ScraperConfigRow): ScraperChannel {
  return {
    type: row.scraper,
    label: titleizeScraper(row.scraper),
    group: 'Configured',
    sourceType: row.source_type,
    itemType: row.item_type,
    description: '从现有配置迁移的抓取器。',
    defaultInput: {},
    hint: '{ ...input }'
  }
}

export default function ScraperDashboard({ authUser, onLogout }: ScraperDashboardProps) {
  const [configs, setConfigs] = useState<ScraperConfigRow[]>([])
  const [itemTypes, setItemTypes] = useState<ItemTypeRow[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | number | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [selectedType, setSelectedType] = useState(scraperChannels[0]?.type || '')
  const [activeView, setActiveView] = useState<ActiveView>('configs')
  const [modal, setModal] = useState<ModalState | null>(null)
  const [copied, setCopied] = useState(false)
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(getDefaultCollapsedGroups)

  const loadConfigs = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [rows, types] = await Promise.all([listScraperConfigs(), listItemTypes()])
      setConfigs(rows)
      setItemTypes(types)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadConfigs()
  }, [loadConfigs])

  const allChannels = useMemo(() => {
    const channels = new Map<string, ScraperChannel>()
    for (const channel of scraperChannels) {
      channels.set(channel.type, channel)
    }
    for (const row of configs) {
      if (!channels.has(row.scraper)) {
        channels.set(row.scraper, channelFromConfig(row))
      }
    }
    return Array.from(channels.values())
  }, [configs])

  const selectedChannel = useMemo(
    () => allChannels.find((channel) => channel.type === selectedType) || allChannels[0] || scraperChannels[0],
    [allChannels, selectedType]
  )

  const channelsByGroup = useMemo(() => {
    const groups = new Map<string, ScraperChannel[]>()
    for (const channel of allChannels) {
      const items = groups.get(channel.itemType) || []
      items.push(channel)
      groups.set(channel.itemType, items)
    }
    return Array.from(groups.entries()).sort(([left], [right]) => left.localeCompare(right))
  }, [allChannels])

  const visibleEnabledConfigs = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return configs
      .filter((config) => config.enabled)
      .filter((config) => {
        if (!normalizedQuery) return true
        return (
          config.name.toLowerCase().includes(normalizedQuery) ||
          config.scraper.toLowerCase().includes(normalizedQuery) ||
          config.sub_source_type.toLowerCase().includes(normalizedQuery)
        )
      })
      .sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name))
  }, [configs, query])

  const selectedConfigs = useMemo(
    () => configs.filter((config) => config.scraper === selectedType),
    [configs, selectedType]
  )

  const stats = useMemo(
    () => ({
      total: configs.length,
      enabled: configs.filter((config) => config.enabled).length,
      channels: new Set(configs.map((config) => config.scraper)).size
    }),
    [configs]
  )

  async function saveDraft(draft: ScraperConfigDraft) {
    await saveScraperConfig(draft)
    setModal(null)
    await loadConfigs()
  }

  async function toggleRow(row: ScraperConfigRow, enabled: boolean) {
    setBusyId(row.id)
    setError('')
    try {
      await setScraperConfigEnabled(row.id, enabled)
      setConfigs((current) =>
        current.map((item) => (item.id === row.id ? { ...item, enabled } : item))
      )
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : String(toggleError))
    } finally {
      setBusyId(null)
    }
  }

  async function removeRow(row: ScraperConfigRow) {
    if (!window.confirm(`确定要删除 "${row.name}" 吗？`)) return

    setBusyId(row.id)
    setError('')
    try {
      await deleteScraperConfig(row.id)
      setConfigs((current) => current.filter((item) => item.id !== row.id))
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : String(deleteError))
    } finally {
      setBusyId(null)
    }
  }

  async function copyRuntimeJson() {
    const runtimeConfigs = visibleEnabledConfigs.map(rowToRuntimeConfig)
    await navigator.clipboard.writeText(JSON.stringify(runtimeConfigs, null, 2))
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }

  function toggleGroup(group: string) {
    setCollapsedGroups((current) => {
      const next = new Set(current)
      if (next.has(group)) {
        next.delete(group)
      } else {
        next.add(group)
      }
      return next
    })
  }

  return (
    <main className="app-shell">
      <aside className="sidebar" aria-label="抓取渠道">
        <div className="sidebar-head">
          <div className="brand-lockup">
            <div className="brand-dot">
              <Code2 size={18} aria-hidden="true" />
            </div>
            <div>
              <strong>Octopus</strong>
              <span>Scraper Admin</span>
            </div>
          </div>
          <button className="icon-button" type="button" aria-label="刷新配置" onClick={() => void loadConfigs()}>
            <RefreshCw size={17} aria-hidden="true" />
          </button>
        </div>

        <nav className="sidebar-nav" aria-label="配置视图">
          <button
            type="button"
            className={`sidebar-nav-item ${activeView === 'effective' ? 'active' : ''}`}
            onClick={() => setActiveView('effective')}
          >
            <CheckCircle2 size={16} aria-hidden="true" />
            <span>当前生效的抓取配置</span>
            <em>{stats.enabled}</em>
          </button>
        </nav>

        <div className="channel-list">
          {channelsByGroup.map(([itemType, channels]) => (
            <section className="channel-group" key={itemType}>
              <button
                type="button"
                className="group-toggle"
                aria-expanded={!collapsedGroups.has(itemType)}
                onClick={() => toggleGroup(itemType)}
              >
                <span>
                  {collapsedGroups.has(itemType) ? (
                    <ChevronRight size={14} aria-hidden="true" />
                  ) : (
                    <ChevronDown size={14} aria-hidden="true" />
                  )}
                  {itemType.toUpperCase()}
                </span>
                <em>
                  {countEnabledByItemType(configs, itemType)}/{countByItemType(configs, itemType)}
                </em>
              </button>
              {!collapsedGroups.has(itemType) ? (
                <div className="channel-group-items">
                  {channels.map((channel) => {
                    const enabledCount = countEnabledByScraper(configs, channel.type, itemType)
                    const totalCount = countByScraper(configs, channel.type, itemType)
                    const active = activeView === 'configs' && selectedType === channel.type

                    return (
                      <button
                        key={channel.type}
                        type="button"
                        className={`channel-item ${active ? 'active' : ''}`}
                        onClick={() => {
                          setSelectedType(channel.type)
                          setActiveView('configs')
                        }}
                      >
                        <div>
                          <span>{channel.label}</span>
                          <small>{channel.type}</small>
                        </div>
                        <em>{enabledCount}/{totalCount}</em>
                      </button>
                    )
                  })}
                </div>
              ) : null}
            </section>
          ))}
        </div>

        <div className="profile-box">
          {authUser.avatarUrl ? (
            <img src={authUser.avatarUrl} alt="" />
          ) : (
            <div className="avatar-fallback">{authUser.name.charAt(0).toUpperCase()}</div>
          )}
          <div>
            <strong>{authUser.name}</strong>
            <span>
              <Github size={12} aria-hidden="true" /> @{authUser.login}
            </span>
          </div>
          <button className="icon-button" type="button" aria-label="退出登录" onClick={onLogout}>
            <LogOut size={16} aria-hidden="true" />
          </button>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <div className="eyebrow">Scraper Configs</div>
            <h1>抓取配置管理</h1>
          </div>
          <div className="stat-strip" aria-label="配置统计">
            <div>
              <span>{stats.total}</span>
              <small>全部配置</small>
            </div>
            <div>
              <span>{stats.enabled}</span>
              <small>当前生效</small>
            </div>
            <div>
              <span>{stats.channels}</span>
              <small>已配置渠道</small>
            </div>
          </div>
        </header>

        {error ? (
          <div className="notice notice-error">
            <AlertCircle size={18} aria-hidden="true" />
            <span>{error}</span>
          </div>
        ) : null}

        <div className="content-grid">
          {activeView === 'configs' ? (
            <section className="channel-detail" aria-labelledby="channel-title">
              <div className="section-head">
                <div>
                  <div className="eyebrow">Selected Channel</div>
                  <h2 id="channel-title">{selectedChannel.label}</h2>
                </div>
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => setModal({ mode: 'create', channel: selectedChannel })}
                >
                  <Plus size={16} aria-hidden="true" />
                  <span>新增配置</span>
                </button>
              </div>

              <p className="channel-desc">{selectedChannel.description}</p>
              <div className="channel-meta">
                <span>{selectedChannel.sourceType}</span>
                <span>{selectedChannel.itemType}</span>
                <code>{selectedChannel.hint}</code>
              </div>

              <div className="local-config-list">
                {selectedConfigs.length === 0 ? (
                  <div className="empty-inline">
                    <LayoutList size={22} aria-hidden="true" />
                    <span>这个渠道还没有配置。</span>
                  </div>
                ) : (
                  selectedConfigs.map((row) => (
                    <article className={`local-config ${row.enabled ? '' : 'disabled'}`} key={row.id}>
                      <div>
                        <strong>{row.name}</strong>
                        <span>P{row.priority} · {row.sub_source_type} · {formatTime(row.updated_date || row.created_date)}</span>
                      </div>
                      <div className="row-actions">
                        <ToggleSwitch
                          checked={row.enabled}
                          disabled={busyId === row.id}
                          label={`切换 ${row.name} 启用状态`}
                          onChange={(enabled) => void toggleRow(row, enabled)}
                        />
                        <button
                          className="icon-button"
                          type="button"
                          aria-label={`编辑 ${row.name}`}
                          onClick={() => setModal({ mode: 'edit', channel: selectedChannel, row })}
                        >
                          <Edit3 size={16} aria-hidden="true" />
                        </button>
                        <button
                          className="icon-button danger"
                          type="button"
                          aria-label={`删除 ${row.name}`}
                          onClick={() => void removeRow(row)}
                        >
                          <Trash2 size={16} aria-hidden="true" />
                        </button>
                      </div>
                    </article>
                  ))
                )}
              </div>
            </section>
          ) : (
            <section className="effective-panel" aria-labelledby="effective-title">
              <div className="section-head">
                <div>
                  <div className="eyebrow">Enabled Now</div>
                  <h2 id="effective-title">当前生效的抓取配置</h2>
                </div>
                <button className="secondary-button" type="button" onClick={copyRuntimeJson}>
                  <Copy size={16} aria-hidden="true" />
                  <span>{copied ? '已复制' : '导出 JSON'}</span>
                </button>
              </div>

              <div className="search-box">
                <Search size={16} aria-hidden="true" />
                <input
                  value={query}
                  placeholder="搜索生效配置"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>

              <div className="effective-list" aria-live="polite">
                {loading ? (
                  <div className="empty-state">
                    <RefreshCw className="spin" size={26} aria-hidden="true" />
                    <span>加载配置中...</span>
                  </div>
                ) : visibleEnabledConfigs.length === 0 ? (
                  <div className="empty-state">
                    <XCircle size={28} aria-hidden="true" />
                    <span>没有匹配的生效配置。</span>
                  </div>
                ) : (
                  visibleEnabledConfigs.map((row) => {
                    const channel = allChannels.find((item) => item.type === row.scraper) || getChannel(row.scraper)
                    return (
                      <article className="effective-row" key={row.id}>
                        <div className="status-icon">
                          <CheckCircle2 size={18} aria-hidden="true" />
                        </div>
                        <div className="effective-main">
                          <div className="effective-title">
                            <strong>{row.name}</strong>
                            <span>{channel?.label || row.scraper}</span>
                          </div>
                          <div className="effective-meta">
                            <span>P{row.priority}</span>
                            <span>{row.source_type}</span>
                            <span>{row.item_type}</span>
                            <span>{row.sub_source_type}</span>
                            <span>{formatTime(row.updated_date || row.created_date)}</span>
                          </div>
                        </div>
                        <button
                          className="icon-button"
                          type="button"
                          aria-label={`编辑 ${row.name}`}
                          onClick={() =>
                            setModal({
                              mode: 'edit',
                              channel: channel || selectedChannel,
                              row
                            })
                          }
                        >
                          <Edit3 size={16} aria-hidden="true" />
                        </button>
                      </article>
                    )
                  })
                )}
              </div>
            </section>
          )}
        </div>
      </section>

      {modal ? (
        <ConfigModal
          {...modal}
          channels={allChannels}
          itemTypes={itemTypes}
          onClose={() => setModal(null)}
          onSave={saveDraft}
        />
      ) : null}
    </main>
  )
}
