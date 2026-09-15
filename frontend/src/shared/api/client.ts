import type { ReadinessResult, HealthReport, RuntimeInfo } from './types'

/**
 * 统一请求封装（E-7 裁决：跟随用户项目 shared/api/client 模式）。
 *
 * 错误分类在这里一次做完（子 Issue E §三）：调用方拿到的是带分类的
 * ApiError，UI 层按 kind 分发——横幅（闸门）/ toast（服务故障）/
 * 行内文案（用户错误），不再各自解析 HTTP 状态码。
 */

export type ApiErrorKind =
  | 'user'        // 400：后端 error 文案面向用户，直接展示
  | 'missing'     // 404：不存在或已删，列表场景刷新
  | 'conflict'    // 409：在途冲突（已在处理中）
  | 'gate'        // 503 带 state：闸门未就绪，触发横幅
  | 'upstream'    // 502：RAG 服务不可达
  | 'server'      // 其余 5xx / 网络错误 / 响应畸形

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number
  readonly state?: string

  constructor(kind: ApiErrorKind, message: string, status: number, state?: string) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.state = state
  }
}

/** 错误响应体统一为 { error: string, state?: string }（各 Controller 的 ExceptionHandler 契约）。 */
interface ErrorBody {
  error?: unknown
  state?: unknown
}

export function classifyFailure(status: number, body: ErrorBody, fallbackMessage: string): ApiError {
  const message = typeof body.error === 'string' && body.error ? body.error : fallbackMessage
  const state = typeof body.state === 'string' ? body.state : undefined
  if (status === 400) return new ApiError('user', message, status)
  if (status === 404) return new ApiError('missing', message, status)
  if (status === 409) return new ApiError('conflict', message, status, state)
  if (status === 503 && state) return new ApiError('gate', message, status, state)
  if (status === 502) return new ApiError('upstream', message, status)
  return new ApiError('server', message, status)
}

async function readErrorBody(response: Response): Promise<ErrorBody> {
  try {
    return (await response.json()) as ErrorBody
  } catch {
    return {}
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (networkFailure) {
    throw new ApiError('server', '无法连接服务器，请确认服务已启动', 0)
  }
  if (!response.ok) {
    const body = await readErrorBody(response)
    throw classifyFailure(response.status, body, `请求失败（HTTP ${response.status}）`)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function getHealth(): Promise<HealthReport> {
  return request<HealthReport>('/health')
}

export function confirmReadiness(): Promise<ReadinessResult> {
  return request<ReadinessResult>('/api/admin/ready', { method: 'POST' })
}

export function getRuntime(): Promise<RuntimeInfo> {
  return request<RuntimeInfo>('/api/runtime')
}
