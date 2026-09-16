/**
 * API 类型定义：与统一 FastAPI 的公开响应对应（子 Issue E §五契约）。
 * 后端是唯一权威——字段改名必须在后端 PR 里做，这里跟随。
 */

// ---- 文档 ----

export interface DocumentCreated {
  id: number
  title: string
  source_type: 'UPLOAD' | 'URL'
  index_status: IndexStatus
}

export type IndexStatus = 'PENDING' | 'INDEXING' | 'INDEXED' | 'FAILED'

export interface DocumentSummary {
  id: number
  title: string
  source_type: string
  tags: string[]
  index_status: IndexStatus
  chunk_count: number
  updated_at: string
}

export interface DocumentPage {
  total: number
  items: DocumentSummary[]
}

export interface DocumentDetail {
  id: number
  title: string
  content: string
  tags: string[]
  source_type: string
  source_uri: string
  index_status: IndexStatus
  index_error: string | null
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface ChunkRow {
  id: number
  seq: number
  text: string
  byte_start: number
  byte_end: number
  heading_path: string
  token_count: number
}

export interface ChunkList {
  items: ChunkRow[]
}

export interface UpdateResult {
  id: number
  index_status: IndexStatus
  reindexed: boolean
}

export interface ReadinessResult {
  state: string
  recovered: number
}

// ---- 问答 ----

export type AnswerStatus = 'ANSWERED' | 'PARTIAL' | 'REFUSED'

export interface ChunkSource {
  chunk_id: number
  document_id: number
  title: string
  text: string
  byte_start: number
  byte_end: number
  heading_path: string
}

export interface RetrievedRef {
  chunk_id: number
  document_id: number
  score: number
  rank: number
}

export interface TraceEntry {
  round_index: number
  query: string
  retrieved: RetrievedRef[]
  decision: 'SUFFICIENT' | 'PARTIAL' | 'NONE'
}

/** trace 是后端透传的 JSON（QuestionController.AnsweredQuestion.trace），
 *  字段契约由 Python QaTraceEntry 定义（qa.py）。 */
export interface AnsweredQuestion {
  answer: string
  status: AnswerStatus
  sources: ChunkSource[]
  trace: TraceEntry[]
}


export interface QuestionModel {
  provider: string
  model: string
}

export interface QuestionMetadata {
  created_at: string
  model: QuestionModel
  elapsed_ms: number
}

export interface SavedQuestion extends AnsweredQuestion, QuestionMetadata {
  history_id: number
}

export interface QuestionHistorySummary extends QuestionMetadata {
  id: number
  question: string
  status: AnswerStatus
}

export interface QuestionHistoryPage {
  total: number
  items: QuestionHistorySummary[]
}

export interface QuestionHistoryDetail extends AnsweredQuestion, QuestionHistorySummary {}

// ---- 健康检查 ----

export interface HealthReport {
  status: string
  service: string
  db?: { database: string; status: string }
}

export interface RuntimeInfo {
  state: 'RECOVERY_REQUIRED' | 'READY' | 'QUERYING' | 'MUTATING' | 'RECOVERING'
  rag_available: boolean
  llm: { configured: boolean; provider: string | null; model: string | null } | null
  embedding: { provider: string; model: string; dim: number } | null
}
