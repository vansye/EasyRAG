import { ApiError, classifyFailure, request } from '@/shared/api/client'
import type { QuestionHistoryDetail, QuestionHistoryPage, SavedQuestion } from '@/shared/api/types'

export function askQuestion(question: string): Promise<SavedQuestion> {
  return request<SavedQuestion>('/api/questions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
}

export async function askQuestionStream(question: string, onDelta: (text: string) => void): Promise<SavedQuestion> {
  const incomplete = () => new ApiError('server', '这次回答没有完成，请检查历史后再决定是否重试。', 0)
  let response: Response
  try {
    response = await fetch('/api/questions/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question }),
    })
  } catch {
    throw incomplete()
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw classifyFailure(response.status, body, '问答请求失败，请重试。')
  }
  if (!response.body || !response.headers.get('content-type')?.startsWith('text/event-stream')) throw incomplete()
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) throw incomplete()
      buffer += decoder.decode(value, { stream: true })
      let boundary: RegExpExecArray | null
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const lines = block.split(/\r?\n/)
        const kind = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
        const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!kind && !data) continue
        const parsed = JSON.parse(data)
        if (kind === 'delta' && typeof parsed === 'string') onDelta(parsed)
        else if (kind === 'sources' && Array.isArray(parsed)) continue
        else if (kind === 'error' && parsed && typeof parsed.error === 'string') {
          throw classifyFailure(parsed.state ? 503 : 502, parsed, '这次回答没有完成。')
        } else if (kind === 'done' && parsed && typeof parsed.answer === 'string'
          && ['ANSWERED', 'PARTIAL', 'REFUSED'].includes(parsed.status)
          && Number.isSafeInteger(parsed.history_id) && parsed.history_id > 0
          && Array.isArray(parsed.sources) && Array.isArray(parsed.trace)
          && typeof parsed.created_at === 'string' && typeof parsed.elapsed_ms === 'number'
          && typeof parsed.model?.provider === 'string' && typeof parsed.model?.model === 'string') {
          return parsed as SavedQuestion
        } else throw incomplete()
      }
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw incomplete()
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export function listQuestionHistory(page: number, size: number): Promise<QuestionHistoryPage> {
  return request<QuestionHistoryPage>(`/api/question-history?page=${page}&size=${size}`)
}

export function getQuestionHistory(id: number): Promise<QuestionHistoryDetail> {
  return request<QuestionHistoryDetail>(`/api/question-history/${id}`)
}

export function deleteQuestionHistory(id: number): Promise<void> {
  return request<void>(`/api/question-history/${id}`, { method: 'DELETE' })
}
