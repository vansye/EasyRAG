import { request } from '@/shared/api/client'
import type { AnsweredQuestion } from '@/shared/api/types'

export function askQuestion(question: string): Promise<AnsweredQuestion> {
  return request<AnsweredQuestion>('/api/questions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
}
