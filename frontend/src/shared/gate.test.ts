import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { classifyFailure } from './api/client'
import { useGateStore } from './gate'

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('runtime refresh', () => {
  it('waits for an in-flight refresh before applying a newly saved model', async () => {
    const gate = useGateStore()
    const runtime = {
      state: 'READY' as const, rag_available: true,
      llm: { configured: true, provider: 'openai', model: 'old-model' },
      embedding: { provider: 'ollama', model: 'test-embedding', dim: 1024 },
    }
    gate.runtime = structuredClone(runtime)
    let releaseHealth!: (response: Response) => void
    const health = new Promise<Response>((resolve) => { releaseHealth = resolve })
    const fetch = vi.fn((path: string) => path === '/health'
      ? health : Promise.resolve(new Response(JSON.stringify(runtime))))
    vi.stubGlobal('fetch', fetch)

    const earlierRefresh = gate.refresh()
    let savedModelApplied = false
    const applySavedModel = gate.refresh().then(() => {
      gate.runtime!.llm = { configured: true, provider: 'openai', model: 'saved-model' }
      savedModelApplied = true
    })
    // Allow settled microtasks to run while the original health request is held.
    await new Promise((resolve) => setTimeout(resolve, 0))
    const appliedBeforeHealth = savedModelApplied
    releaseHealth(new Response(JSON.stringify({ status: 'UP', db: { status: 'UP' } })))
    await Promise.all([earlierRefresh, applySavedModel])

    expect(appliedBeforeHealth).toBe(false)
    expect(gate.modelName).toBe('saved-model')
    expect(gate.canAsk).toBe(true)
    expect(gate.refreshing).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('allows an explicit retry after a failed refresh', async () => {
    const gate = useGateStore()
    const fetch = vi.fn().mockRejectedValue(new Error('offline'))
    vi.stubGlobal('fetch', fetch)
    await gate.refresh()
    expect(gate.connected).toBe(false)
    expect(gate.refreshing).toBe(false)

    fetch.mockImplementation((path: string) => Promise.resolve(new Response(JSON.stringify(path === '/health'
      ? { status: 'UP', db: { status: 'UP' } }
      : { state: 'READY', rag_available: true, llm: { configured: true, provider: 'openai', model: 'online-model' } }))))
    await gate.refresh()

    expect(gate.connected).toBe(true)
    expect(gate.modelName).toBe('online-model')
    expect(gate.connectionError).toBe('')
    expect(fetch).toHaveBeenCalledTimes(4)
  })
})

describe('operation conflicts', () => {
  it.each(['MUTATING', 'RECOVERY_REQUIRED'] as const)('pauses questions on a 409 carrying %s', (state) => {
    const gate = useGateStore()
    gate.connected = true
    gate.runtime = {
      state: 'READY',
      rag_available: true,
      llm: { configured: true, provider: 'openai', model: 'test-model' },
      embedding: { provider: 'ollama', model: 'test-embedding', dim: 1024 },
    }
    expect(gate.canAsk).toBe(true)

    gate.raise(classifyFailure(409, { error: '资料暂时无法修改', state }, '请求失败'))

    expect(gate.runtime.state).toBe(state)
    expect(gate.visible).toBe(true)
    expect(gate.canAsk).toBe(false)
    expect(gate.needsConfirmation).toBe(state === 'RECOVERY_REQUIRED')
  })

  it('keeps document-only conflicts local when no operation state is provided', () => {
    const gate = useGateStore()
    gate.blockedState = 'READY'
    const error = classifyFailure(409, { error: '这份资料正在处理' }, '请求失败')

    gate.raise(error)

    expect(error.kind).toBe('conflict')
    expect(gate.blockedState).toBe('READY')
  })
})
