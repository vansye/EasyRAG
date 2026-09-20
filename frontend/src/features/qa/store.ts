import { defineStore } from 'pinia'

import type { QuestionHistoryDetail, QuestionHistorySummary, SavedQuestion } from '@/shared/api/types'
import { useGateStore } from '@/shared/gate'
import { askQuestionStream, deleteQuestionHistory, getQuestionHistory, listQuestionHistory } from './api'

export const useQuestionStore = defineStore('questions', {
  state: () => ({
    draft: '',
    answeredQuestion: '',
    pendingQuestion: '',
    previewText: '',
    firstTextMs: null as number | null,
    result: null as SavedQuestion | QuestionHistoryDetail | null,
    loading: false,
    startedAt: 0,
    error: '',
    sourceChanged: false,
    historyMode: false,
    selectedHistoryId: null as number | null,
    selectionRevision: 0,
    historyItems: [] as QuestionHistorySummary[],
    historyTotal: 0,
    historyPage: 0,
    historySize: 20,
    historyLoading: false,
    historyError: '',
    historyRequestId: 0,
    detailLoading: false,
    detailError: '',
    deletingHistoryId: null as number | null,
    historyDeletePending: false,
    latestSavedId: null as number | null,
  }),
  getters: {
    usedModel: (state) => state.result ? `${state.result.model.provider} · ${state.result.model.model}` : '',
    elapsedMs: (state) => state.result?.elapsed_ms ?? 0,
  },
  actions: {
    async submit() {
      const question = this.draft.trim()
      if (!question || this.loading) return
      if ([...question].length > 2000) {
        this.error = '问题请控制在 2000 字以内。'
        return
      }
      const gate = useGateStore()
      if (!gate.canAsk) {
        this.error = `${gate.statusLabel}，就绪后请重试。`
        return
      }
      this.resetSelection()
      const revision = this.selectionRevision
      this.loading = true
      this.error = ''
      this.latestSavedId = null
      this.pendingQuestion = question
      this.startedAt = Date.now()
      try {
        const answer = await askQuestionStream(question, text => {
          if (revision !== this.selectionRevision) return
          if (this.firstTextMs === null && text.trim()) this.firstTextMs = Date.now() - this.startedAt
          this.previewText += text
        })
        if (revision === this.selectionRevision) {
          this.result = answer
          this.answeredQuestion = question
          this.selectedHistoryId = answer.history_id
          this.previewText = ''
        } else {
          this.latestSavedId = answer.history_id
        }
        void this.loadHistory(0)
      } catch (failure) {
        gate.raise(failure)
        const message = failure instanceof Error ? failure.message : '这次回答没有完成，请重试。'
        this.error = revision === this.selectionRevision ? message : `另一条问题「${question}」的回答未完成：${message}`
      } finally {
        this.loading = false
        void gate.refresh()
      }
    },
    async loadHistory(page = this.historyPage) {
      const requestId = ++this.historyRequestId
      this.historyLoading = true
      this.historyError = ''
      try {
        const result = await listQuestionHistory(page, this.historySize)
        if (requestId !== this.historyRequestId || this.historyDeletePending) return
        this.historyItems = result.items
        this.historyTotal = result.total
        this.historyPage = page
      } catch (failure) {
        if (requestId !== this.historyRequestId || this.historyDeletePending) return
        this.historyError = failure instanceof Error ? failure.message : '历史记录读取失败，请重试。'
      } finally {
        if (requestId === this.historyRequestId) this.historyLoading = false
      }
    },
    async openHistory(id: number) {
      if (this.deletingHistoryId === id) return
      this.resetSelection()
      const revision = this.selectionRevision
      this.historyMode = true
      this.selectedHistoryId = id
      this.detailLoading = true
      this.error = ''
      if (this.latestSavedId === id) this.latestSavedId = null
      try {
        const record = await getQuestionHistory(id)
        if (revision !== this.selectionRevision) return
        this.result = record
        this.answeredQuestion = record.question
      } catch (failure) {
        if (revision !== this.selectionRevision) return
        this.detailError = failure instanceof Error ? failure.message : '这条历史记录读取失败，请重试。'
      } finally {
        if (revision === this.selectionRevision) this.detailLoading = false
      }
    },
    async removeHistory(id: number) {
      if (this.deletingHistoryId !== null) return
      this.deletingHistoryId = id
      this.historyDeletePending = true
      this.historyError = ''
      try {
        await deleteQuestionHistory(id)
        this.historyRequestId++
        this.historyDeletePending = false
        this.historyItems = this.historyItems.filter((item) => item.id !== id)
        this.historyTotal -= 1
        if (this.selectedHistoryId === id) this.resetSelection()
        if (this.latestSavedId === id) this.latestSavedId = null
        const lastPage = Math.max(0, Math.ceil(this.historyTotal / this.historySize) - 1)
        this.historyPage = Math.min(this.historyPage, lastPage)
        await this.loadHistory()
      } catch (failure) {
        this.historyError = failure instanceof Error ? failure.message : '删除失败，请重试。'
      } finally {
        this.historyDeletePending = false
        this.deletingHistoryId = null
      }
    },
    resetSelection() {
      this.selectionRevision++
      this.selectedHistoryId = null
      this.historyMode = false
      this.detailLoading = false
      this.detailError = ''
      this.result = null
      this.previewText = ''
      this.firstTextMs = null
      this.answeredQuestion = ''
      this.sourceChanged = false
    },
    clear() {
      if (this.loading) return
      this.resetSelection()
      this.draft = ''
      this.error = ''
      this.latestSavedId = null
    },
    markSourceChanged(documentId: number) {
      if (!this.historyMode && this.result?.sources.some((source) => source.document_id === documentId)) this.sourceChanged = true
    },
  },
})
