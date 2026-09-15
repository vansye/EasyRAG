import { defineStore } from 'pinia'

import type { AnsweredQuestion } from '@/shared/api/types'
import { useGateStore } from '@/shared/gate'
import { askQuestion } from './api'

export const useQuestionStore = defineStore('questions', {
  state: () => ({
    draft: '',
    answeredQuestion: '',
    pendingQuestion: '',
    result: null as AnsweredQuestion | null,
    loading: false,
    startedAt: 0,
    elapsedMs: 0,
    error: '',
    usedModel: '',
    sourceChanged: false,
  }),
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
      this.loading = true
      this.error = ''
      this.pendingQuestion = question
      this.startedAt = Date.now()
      const model = gate.modelName
      try {
        const answer = await askQuestion(question)
        this.result = answer
        this.answeredQuestion = question
        this.usedModel = model
        this.sourceChanged = false
        this.elapsedMs = Date.now() - this.startedAt
      } catch (failure) {
        gate.raise(failure)
        this.error = failure instanceof Error ? failure.message : '这次回答没有完成，请重试。'
      } finally {
        this.loading = false
        void gate.refresh()
      }
    },
    clear() {
      if (this.loading) return
      this.draft = ''
      this.result = null
      this.error = ''
      this.answeredQuestion = ''
      this.sourceChanged = false
    },
    markSourceChanged(documentId: number) {
      if (this.result?.sources.some((source) => source.document_id === documentId)) this.sourceChanged = true
    },
  },
})
