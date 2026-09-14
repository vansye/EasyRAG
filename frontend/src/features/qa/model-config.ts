import { request } from '@/shared/api/client'

export type ModelProvider = 'openai' | 'deepseek'

export interface ModelConfig {
  configured: boolean
  provider: ModelProvider
  model: string
  base_url: string
  api_key_configured: boolean
  source: 'environment' | 'local'
}

export interface ModelConfigUpdate {
  provider: ModelProvider
  model: string
  base_url: string
  api_key: string
}

export const modelDefaultUrls: Record<ModelProvider, string> = {
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com/v1',
}

function normalizedAddress(value: string): string {
  try {
    return new URL(value.trim()).toString().replace(/\/+$/, '')
  } catch {
    return value.trim()
  }
}

export function canKeepModelKey(config: ModelConfig | null, provider: ModelProvider, address: string): boolean {
  return config?.api_key_configured === true && config.provider === provider
    && normalizedAddress(config.base_url) === normalizedAddress(address)
}

export function getModelConfig(): Promise<ModelConfig> {
  return request<ModelConfig>('/api/model-config', { cache: 'no-store' })
}

export function saveModelConfig(config: ModelConfigUpdate): Promise<ModelConfig> {
  return request<ModelConfig>('/api/model-config', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config),
  })
}

export function resetModelConfig(): Promise<ModelConfig> {
  return request<ModelConfig>('/api/model-config', { method: 'DELETE' })
}
