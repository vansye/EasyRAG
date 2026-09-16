import { request } from '@/shared/api/client'
import type { QuestionHistoryDetail, QuestionHistoryPage, SavedQuestion } from '@/shared/api/types'

export function askQuestion(question: string): Promise<SavedQuestion> {
  return request<SavedQuestion>('/api/questions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
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
