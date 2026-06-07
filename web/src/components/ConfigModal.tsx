import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Save, X } from 'lucide-react'
import type { JsonValue, ScraperConfigDraft, ScraperConfigRow } from '../types'
import type { ScraperChannel } from '../types'
import { getChannel, scraperChannels } from '../lib/channels'
import ToggleSwitch from './ToggleSwitch'

type ConfigModalProps = {
  mode: 'create' | 'edit'
  channel: ScraperChannel
  row?: ScraperConfigRow
  onClose: () => void
  onSave: (draft: ScraperConfigDraft) => Promise<void>
}

function cloneConfig(config: Record<string, JsonValue>) {
  return JSON.parse(JSON.stringify(config)) as Record<string, JsonValue>
}

function defaultName(channel: ScraperChannel) {
  return channel.type === 'rss' ? 'New RSS Source' : channel.label
}

export default function ConfigModal({
  mode,
  channel,
  row,
  onClose,
  onSave
}: ConfigModalProps) {
  const initialChannel = row ? getChannel(row.scraper_type) || channel : channel
  const [scraperType, setScraperType] = useState(row?.scraper_type || initialChannel.type)
  const [name, setName] = useState(row?.name || defaultName(initialChannel))
  const [priority, setPriority] = useState(row?.priority ?? 10)
  const [enabled, setEnabled] = useState(row?.enabled ?? true)
  const [jsonText, setJsonText] = useState(
    JSON.stringify(row?.config || initialChannel.defaultConfig, null, 2)
  )
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState(false)

  const activeChannel = useMemo(() => getChannel(scraperType) || channel, [channel, scraperType])

  useEffect(() => {
    if (mode === 'edit') return
    setJsonText(JSON.stringify(cloneConfig(activeChannel.defaultConfig), null, 2))
    setName(defaultName(activeChannel))
  }, [activeChannel, mode])

  async function handleSave() {
    let parsed: unknown
    try {
      parsed = JSON.parse(jsonText)
    } catch (jsonError) {
      setError(`JSON 格式错误: ${jsonError instanceof Error ? jsonError.message : String(jsonError)}`)
      return
    }

    if (!name.trim()) {
      setError('名称不能为空')
      return
    }
    if (!scraperType.trim()) {
      setError('抓取渠道不能为空')
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('Config 必须是一个 JSON object')
      return
    }

    setSaving(true)
    setError('')
    try {
      await onSave({
        id: row?.id,
        name: name.trim(),
        scraper_type: scraperType,
        enabled,
        priority,
        config: parsed as Record<string, JsonValue>
      })
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError))
      setSaving(false)
    }
  }

  async function copyJson() {
    await navigator.clipboard.writeText(jsonText)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }

  return (
    <div className="modal-layer" role="presentation">
      <section className="config-modal" role="dialog" aria-modal="true" aria-labelledby="config-modal-title">
        <header className="modal-header">
          <div>
            <div className="eyebrow">{mode === 'create' ? 'Create Config' : 'Edit Config'}</div>
            <h2 id="config-modal-title">{mode === 'create' ? '新增抓取配置' : '编辑抓取配置'}</h2>
          </div>
          <button className="icon-button" type="button" aria-label="关闭" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="modal-grid">
          <label className="field">
            <span>名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>

          <label className="field">
            <span>渠道</span>
            <select value={scraperType} onChange={(event) => setScraperType(event.target.value)}>
              {scraperChannels.map((item) => (
                <option value={item.type} key={item.type}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>优先级</span>
            <input
              type="number"
              min="0"
              max="999"
              value={priority}
              onChange={(event) => setPriority(Number(event.target.value))}
            />
          </label>

          <div className="field switch-field">
            <span>启用</span>
            <ToggleSwitch checked={enabled} label="切换启用状态" onChange={setEnabled} />
          </div>
        </div>

        <div className="schema-hint">
          <span>{activeChannel.label}</span>
          <code>{activeChannel.hint}</code>
        </div>

        <label className="field json-field">
          <span>Config JSON</span>
          <textarea
            spellCheck={false}
            value={jsonText}
            onChange={(event) => setJsonText(event.target.value)}
          />
        </label>

        {error ? <div className="form-error">{error}</div> : null}

        <footer className="modal-actions">
          <button className="ghost-button" type="button" onClick={copyJson}>
            {copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
            <span>{copied ? '已复制' : '复制 JSON'}</span>
          </button>
          <div className="action-spacer" />
          <button className="secondary-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button" type="button" disabled={saving} onClick={handleSave}>
            <Save size={16} aria-hidden="true" />
            <span>{saving ? '保存中...' : '保存'}</span>
          </button>
        </footer>
      </section>
    </div>
  )
}
