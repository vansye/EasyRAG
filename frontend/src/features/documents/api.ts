import { request } from '@/shared/api/client'
import type {
  ChunkList, DocumentCreated, DocumentDetail, DocumentPage, IndexStatus, UpdateResult,
} from '@/shared/api/types'

/** 上传（multipart，A-2 契约：201 立即返回 PENDING，不等索引）。 */
export function uploadDocument(file: File): Promise<DocumentCreated> {
  const form = new FormData()
  form.append('file', file)
  return request<DocumentCreated>('/api/documents', { method: 'POST', body: form })
}

export function listDocuments(params: {
  status?: IndexStatus
  q?: string
  page?: number
  size?: number
}): Promise<DocumentPage> {
  const search = new URLSearchParams()
  if (params.status) search.set('status', params.status)
  if (params.q) search.set('q', params.q)
  if (params.page !== undefined) search.set('page', String(params.page))
  if (params.size !== undefined) search.set('size', String(params.size))
  const suffix = search.size > 0 ? `?${search}` : ''
  return request<DocumentPage>(`/api/documents${suffix}`)
}

export function getDocument(id: number): Promise<DocumentDetail> {
  return request<DocumentDetail>(`/api/documents/${id}`)
}

export function getChunks(id: number): Promise<ChunkList> {
  return request<ChunkList>(`/api/documents/${id}/chunks`)
}

export function deleteDocument(id: number): Promise<void> {
  return request<void>(`/api/documents/${id}`, { method: 'DELETE' })
}

/** 更新正文（JSON 形态：PUT 的两种形态里，前端编辑场景用 content 字段够用）。 */
export function updateDocumentContent(id: number, content: string): Promise<UpdateResult> {
  return request<UpdateResult>(`/api/documents/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export function reindexDocument(id: number): Promise<UpdateResult> {
  return request<UpdateResult>(`/api/documents/${id}/reindex`, { method: 'POST' })
}
