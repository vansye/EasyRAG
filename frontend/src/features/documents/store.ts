import { defineStore } from 'pinia'

import { useQuestionStore } from '@/features/qa/store'
import type { DocumentSummary, IndexStatus } from '@/shared/api/types'
import { useGateStore } from '@/shared/gate'
import { listDocuments, uploadDocument } from './api'

export const useDocumentStore = defineStore('documents', {
  state: () => ({
    items: [] as DocumentSummary[],
    total: 0,
    allTotal: 0,
    page: 0,
    size: 10,
    search: '',
    filter: '' as '' | IndexStatus,
    loaded: false,
    loading: false,
    uploading: false,
    uploadName: '',
    error: '',
    notice: '',
    requestVersion: 0,
  }),
  getters: {
    hasPending: (state) => state.items.some((document) => ['PENDING', 'INDEXING'].includes(document.index_status)),
    pages: (state) => Math.max(1, Math.ceil(state.total / state.size)),
    uploadBlockedReason(): string {
      const gate = useGateStore()
      if (useQuestionStore().loading || gate.runtime?.state === 'QUERYING') return '当前回答完成后，即可继续添加资料。'
      if (!gate.connected) return '服务连接后，即可添加资料。'
      if (!gate.runtime?.rag_available) return '问答服务连接后，即可处理新资料。'
      if (gate.runtime.state === 'RECOVERY_REQUIRED') return '请先在上方确认知识库就绪。'
      if (gate.runtime.state !== 'READY') return '资料处理完成后，即可继续添加。'
      return ''
    },
    canUpload(state): boolean { return !state.uploading && !this.uploadBlockedReason },
  },
  actions: {
    async load(quiet = false) {
      const version = ++this.requestVersion
      if (!quiet) {
        this.loading = true
        this.error = ''
      }
      try {
        const page = await listDocuments({ page: this.page, size: this.size, status: this.filter || undefined, q: this.search.trim() || undefined })
        if (version !== this.requestVersion) return
        this.items = page.items
        this.total = page.total
        if (!this.filter && !this.search.trim()) this.allTotal = page.total
        this.loaded = true
      } catch (failure) {
        if (version !== this.requestVersion) return
        useGateStore().raise(failure)
        this.error = failure instanceof Error ? failure.message : '资料加载失败，请重试。'
      } finally {
        if (version === this.requestVersion) this.loading = false
      }
    },
    async upload(file: File) {
      if (this.uploading) return
      this.error = ''
      this.notice = ''
      if (this.uploadBlockedReason) {
        this.error = this.uploadBlockedReason
        return
      }
      if (!/\.(md|txt)$/i.test(file.name)) {
        this.error = '目前支持 Markdown（.md）和纯文本（.txt）文件。'
        return
      }
      if (file.size > 1024 * 1024) {
        this.error = '单份资料最大 1 MB，请拆分后上传。'
        return
      }
      this.uploading = true
      this.uploadName = file.name
      try {
        const created = await uploadDocument(file)
        this.page = 0
        this.search = ''
        this.filter = ''
        this.notice = `「${created.title}」已收录，处理完成后即可用于回答。`
        await this.load()
        void useGateStore().refreshAfterChange()
      } catch (failure) {
        useGateStore().raise(failure)
        this.error = failure instanceof Error ? failure.message : '上传失败，请重试。'
      } finally {
        this.uploading = false
        this.uploadName = ''
      }
    },
  },
})
