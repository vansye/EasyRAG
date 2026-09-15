import { defineStore } from 'pinia'

import { ApiError, confirmReadiness, getHealth, getRuntime } from '@/shared/api/client'
import type { RuntimeInfo } from '@/shared/api/types'

const pendingRefreshes = new WeakMap<object, Promise<void>>()

export const useGateStore = defineStore('gate', {
  state: () => ({
    runtime: null as RuntimeInfo | null,
    connected: false,
    checked: false,
    refreshing: false,
    blockedState: '',
    recovering: false,
    recoveryError: '',
    connectionError: '',
    notice: '',
  }),
  getters: {
    visible: (state) => ['RECOVERY_REQUIRED', 'MUTATING', 'RECOVERING'].includes(state.blockedState),
    needsConfirmation: (state) => state.blockedState === 'RECOVERY_REQUIRED',
    modelName: (state) => state.runtime?.llm?.model ?? '',
    canAsk: (state) => state.connected && state.runtime?.rag_available === true
      && state.runtime.llm?.configured === true && ['READY', 'QUERYING'].includes(state.runtime.state),
    statusLabel(state): string {
      if (!state.checked) return '正在连接'
      if (!state.connected) return '服务未连接'
      if (!state.runtime?.rag_available) return '问答服务未连接'
      if (state.blockedState === 'RECOVERY_REQUIRED') return '待确认就绪'
      if (['MUTATING', 'RECOVERING'].includes(state.blockedState)) return '资料处理中'
      if (!state.runtime.llm?.configured) return '问答模型未配置'
      return '服务已连接'
    },
  },
  actions: {
    refresh(): Promise<void> {
      const pending = pendingRefreshes.get(this)
      if (pending) return pending
      this.refreshing = true
      const refresh = (async () => {
        try {
          const [health, runtime] = await Promise.all([getHealth(), getRuntime()])
          this.connected = health.status === 'UP' && health.db?.status === 'UP'
          this.runtime = runtime
          this.blockedState = runtime.state
          this.connectionError = this.connected ? '' : '暂时无法连接资料库，请检查服务后重试。'
        } catch (failure) {
          this.connected = false
          this.connectionError = failure instanceof Error ? failure.message : '暂时无法连接服务。'
        } finally {
          this.checked = true
          this.refreshing = false
          pendingRefreshes.delete(this)
        }
      })()
      pendingRefreshes.set(this, refresh)
      return refresh
    },
    raise(error: unknown) {
      if (error instanceof ApiError && ['gate', 'conflict'].includes(error.kind) && error.state) {
        this.blockedState = error.state
        if (this.runtime) this.runtime.state = error.state as RuntimeInfo['state']
        this.recoveryError = ''
      }
    },
    async recover() {
      if (!this.needsConfirmation || this.recovering) return
      this.recovering = true
      this.recoveryError = ''
      try {
        const result = await confirmReadiness()
        this.notice = result.recovered > 0 ? `知识库已就绪，${result.recovered} 份资料开始处理。` : '知识库已就绪，可以继续提问了。'
        await this.refresh()
      } catch (failure) {
        this.recoveryError = failure instanceof Error ? failure.message : '确认失败，请稍后重试。'
      } finally {
        this.recovering = false
      }
    },
  },
})
