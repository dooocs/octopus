import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Save, X } from 'lucide-react'
import type { ItemTypeRow, JsonValue, ScraperConfigDraft, ScraperConfigRow } from '../types'
import type { ScraperChannel } from '../types'
import ToggleSwitch from './ToggleSwitch'

type ConfigModalProps = {
  mode: 'create' | 'edit'
  channel: ScraperChannel
  channels: ScraperChannel[]
  itemTypes: ItemTypeRow[]
  row?: ScraperConfigRow
  onClose: () => void
  onSave: (draft: ScraperConfigDraft) => Promise<void>
}

function cloneInput(input: Record<string, JsonValue>) {
  return JSON.parse(JSON.stringify(input)) as Record<string, JsonValue>
}

function defaultName(channel: ScraperChannel) {
  return channel.type === 'rss' ? 'New RSS Source' : channel.label
}

function slugify(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

export default function ConfigModal({
  mode,
  channel,
  channels,
  itemTypes,
  row,
  onClose,
  onSave
}: ConfigModalProps) {
  const initialChannel = row ? channels.find((item) => item.type === row.scraper) || channel : channel
  const [scraper, setScraper] = useState(row?.scraper || initialChannel.type)
  const [name, setName] = useState(row?.name || defaultName(initialChannel))
  const [priority, setPriority] = useState(row?.priority ?? 10)
  const [enabled, setEnabled] = useState(row?.enabled ?? true)
  const [sourceType, setSourceType] = useState(row?.source_type || initialChannel.sourceType)
  const [subSourceType, setSubSourceType] = useState(
    row?.sub_source_type || slugify(defaultName(initialChannel)) || initialChannel.type
  )
  const [itemType, setItemType] = useState(row?.item_type || initialChannel.itemType)
  const [jsonText, setJsonText] = useState(
    JSON.stringify(row?.input || initialChannel.defaultInput, null, 2)
  )
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState(false)

  const activeChannel = useMemo(
    () => channels.find((item) => item.type === scraper) || channel,
    [channel, channels, scraper]
  )

  useEffect(() => {
    if (mode === 'edit') return
    const nextName = defaultName(activeChannel)
    setJsonText(JSON.stringify(cloneInput(activeChannel.defaultInput), null, 2))
    setName(nextName)
    setSourceType(activeChannel.sourceType)
    setSubSourceType(slugify(nextName) || activeChannel.type)
    setItemType(activeChannel.itemType)
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
    if (!scraper.trim()) {
      setError('抓取器不能为空')
      return
    }
    if (!sourceType.trim()) {
      setError('source_type 不能为空')
      return
    }
    if (!subSourceType.trim()) {
      setError('sub_source_type 不能为空')
      return
    }
    if (!itemType.trim()) {
      setError('item_type 不能为空')
      return
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('Input 必须是一个 JSON object')
      return
    }

    setSaving(true)
    setError('')
    try {
      await onSave({
        id: row?.id,
        name: name.trim(),
        scraper: scraper.trim(),
        enabled,
        priority,
        source_type: sourceType.trim(),
        sub_source_type: subSourceType.trim(),
        item_type: itemType.trim(),
        input: parsed as Record<string, JsonValue>
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
            <span>抓取器</span>
            <select value={scraper} onChange={(event) => setScraper(event.target.value)}>
              {channels.map((item) => (
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

          <label className="field">
            <span>source_type</span>
            <input value={sourceType} onChange={(event) => setSourceType(event.target.value)} />
          </label>

          <label className="field">
            <span>sub_source_type</span>
            <input value={subSourceType} onChange={(event) => setSubSourceType(event.target.value)} />
          </label>

          <label className="field">
            <span>item_type</span>
            <select value={itemType} onChange={(event) => setItemType(event.target.value)}>
              {itemTypes.length === 0 ? (
                <option value={itemType}>{itemType}</option>
              ) : null}
              {itemTypes.map((item) => (
                <option value={item.item_type} key={item.item_type}>
                  {item.item_type}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="schema-hint">
          <span>{activeChannel.label}</span>
          <code>{activeChannel.hint}</code>
        </div>

        <label className="field json-field">
          <span>Input JSON</span>
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
