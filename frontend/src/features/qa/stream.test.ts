import { afterEach, expect, it, vi } from 'vitest'
import { askQuestionStream } from './api'

afterEach(() => vi.unstubAllGlobals())

const answer = { answer: '你好 [1]', status: 'ANSWERED', sources: [], trace: [], history_id: 1,
  created_at: '2026-09-20T00:00:00', model: { provider: 'openai', model: 'test' }, elapsed_ms: 12 }

function response(text: string, width = 1) {
  const bytes = new TextEncoder().encode(text)
  let offset = 0
  return new Response(new ReadableStream({
    pull(controller) {
      if (offset === bytes.length) return controller.close()
      controller.enqueue(bytes.slice(offset, offset += width))
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } })
}

it('decodes UTF-8 and SSE split across every byte', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(
    'event: sources\ndata: []\n\nevent: delta\ndata: "你好 "\n\n' +
    `event: done\ndata: ${JSON.stringify(answer)}\n\n`,
  )))
  const deltas: string[] = []
  expect(await askQuestionStream('question', text => deltas.push(text))).toEqual(answer)
  expect(deltas).toEqual(['你好 '])
})

it.each([
  'event: delta\ndata: "partial"\n\n',
  'event: done\ndata: {}\n\n',
  'event: delta\ndata: 12\n\n',
  'event: done\ndata: invalid\n\n',
])('never treats incomplete or malformed output as success: %s', async text => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(text)))
  await expect(askQuestionStream('question', () => {})).rejects.toThrow()
})

it('maps an in-stream gate error without pretending it is a refusal', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(
    'event: error\ndata: {"error":"not ready","state":"RECOVERY_REQUIRED"}\n\n',
  )))
  await expect(askQuestionStream('question', () => {})).rejects.toMatchObject({ kind: 'gate', state: 'RECOVERY_REQUIRED' })
})

it('accepts CRLF frames and joined data lines split across reads', async () => {
  const json = JSON.stringify(answer).replace(',"status"', ',\r\ndata: "status"')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(`: keepalive\r\n\r\nevent: done\r\ndata: ${json}\r\n\r\n`, 3)))
  expect(await askQuestionStream('question', () => {})).toEqual(answer)
})

it('delivers a delta while the response is still open and cancels reader on done', async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const cancelled = vi.fn()
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c }, cancel: cancelled })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })))
  const received = vi.fn()
  const result = askQuestionStream('question', received)
  controller.enqueue(new TextEncoder().encode('event: delta\ndata: "first"\n\n'))
  await vi.waitFor(() => expect(received).toHaveBeenCalledWith('first'))
  controller.enqueue(new TextEncoder().encode(`event: done\ndata: ${JSON.stringify(answer)}\n\n`))
  expect(await result).toEqual(answer)
  expect(cancelled).toHaveBeenCalled()
})
