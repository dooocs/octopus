import specs from '../generated/scraper_specs.json'
import type { JsonValue, ScraperChannel } from '../types'

type GeneratedSpec = {
  scraper: string
  label: string
  group: string
  default_source_type: string
  default_item_type: string
  input_schema_version: number
  input_schema: Record<string, JsonValue>
  default_input: Record<string, JsonValue>
  required_secrets: string[]
  supported_enrichers: string[]
  description: string
}

function schemaHint(spec: GeneratedSpec) {
  const properties = spec.input_schema.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) {
    return '{ source, fetch, filters, enrich }'
  }
  return '{ source, fetch, filters, enrich }'
}

export const scraperChannels: ScraperChannel[] = (specs as unknown as GeneratedSpec[]).map((spec) => ({
  type: spec.scraper,
  label: spec.label,
  group: spec.group,
  sourceType: spec.default_source_type,
  itemType: spec.default_item_type,
  description: spec.description,
  defaultInput: spec.default_input,
  inputSchemaVersion: spec.input_schema_version,
  inputSchema: spec.input_schema,
  requiredSecrets: spec.required_secrets,
  supportedEnrichers: spec.supported_enrichers,
  hint: schemaHint(spec)
}))

export function getChannel(type: string) {
  return scraperChannels.find((channel) => channel.type === type)
}
