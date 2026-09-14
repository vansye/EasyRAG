import type { IndexStatus } from '@/shared/api/types'

export function documentStatus(status: IndexStatus): { label: string; tone: string; icon: string } {
  if (status === 'INDEXED') return { label: '可查询', tone: 'ready', icon: 'check' }
  if (status === 'FAILED') return { label: '处理失败', tone: 'failed', icon: 'alert' }
  return { label: '处理中', tone: 'pending', icon: 'clock' }
}

export function displayDate(value: string): string {
  const date = new Date(value)
  const today = new Date()
  if (date.toDateString() === today.toDateString()) {
    return `今天 ${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })}`
  }
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', ...(date.getFullYear() !== today.getFullYear() ? { year: 'numeric' as const } : {}) })
}
