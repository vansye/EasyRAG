<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { useDocumentStore } from '@/features/documents/store'
import type { AnswerStatus } from '@/shared/api/types'
import AppIcon from '@/shared/components/AppIcon.vue'
import { useGateStore } from '@/shared/gate'
import AnswerView from './components/AnswerView.vue'
import QuestionBox from './components/QuestionBox.vue'
import { useQuestionStore } from './store'
import '@/styles/qa.css'

const qa = useQuestionStore()
const library = useDocumentStore()
const gate = useGateStore()
const composer = ref<InstanceType<typeof QuestionBox>>()
const now = ref(Date.now())
const seconds = computed(() => Math.max(0, Math.floor((now.value - qa.startedAt) / 1000)))
const suggestions = computed(() => library.items.slice(0, 3))
const confirmHistoryId = ref<number | null>(null)
const historyPageCount = computed(() => Math.max(1, Math.ceil(qa.historyTotal / qa.historySize)))
const historyStatusLabels: Record<AnswerStatus, string> = { ANSWERED: '已回答', PARTIAL: '部分覆盖', REFUSED: '依据不足' }
let elapsedTimer: ReturnType<typeof setInterval> | undefined

watch(() => qa.loading, (loading) => {
  clearInterval(elapsedTimer)
  now.value = Date.now()
  if (loading) elapsedTimer = setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })

function suggest(title: string) {
  qa.draft = `请根据资料解释「${title}」的主要内容。`
  composer.value?.focus()
}

function newQuestion() {
  qa.clear()
  composer.value?.focus()
}

function retry() {
  qa.draft = qa.answeredQuestion
  void qa.submit()
}

function changeHistoryPage(page = qa.historyPage) {
  confirmHistoryId.value = null
  void qa.loadHistory(page)
}

async function removeHistory(id: number) {
  await qa.removeHistory(id)
  confirmHistoryId.value = null
}

onMounted(() => {
  if (!library.loaded) void library.load(true)
  void qa.loadHistory()
})
onUnmounted(() => clearInterval(elapsedTimer))
</script>

<template>
  <div class="ask-workspace">
  <section class="ask-page page-enter" :class="{ 'has-answer': qa.result || qa.loading || qa.historyMode }">
    <div v-if="!qa.result && !qa.loading && !qa.historyMode" class="ask-welcome">
      <div class="ask-emblem" aria-hidden="true"><span class="emblem-orbit" /><span class="emblem-dot" /><AppIcon name="book" :size="31" /><span class="emblem-spark">✦</span></div>
      <p class="eyebrow">你的资料，值得被再次发现</p>
      <h1>让答案，回到你的资料里<span class="heading-dot">。</span></h1>
      <p class="ask-description">问一个问题，找到那些你收藏过、记下过的知识。<br />每一份依据，都可以回到原文。</p>
    </div>
    <div v-else class="ask-page-topline"><span class="eyebrow"><AppIcon name="chat" :size="16" />知识库问答</span><button class="text-button" :disabled="qa.loading" @click="newQuestion"><AppIcon name="plus" :size="16" />新问题</button></div>

    <QuestionBox ref="composer" />

    <div v-if="qa.error" class="notice notice-error question-error" role="alert"><AppIcon name="info" :size="18" /><span>{{ qa.error }}</span><button class="text-button" :disabled="qa.loading || !gate.canAsk" @click="qa.submit()">再试一次</button></div>

    <div v-if="!qa.result && !qa.loading && !qa.historyMode" class="ask-starters">
      <template v-if="suggestions.length"><p>从你的资料开始</p><div class="suggestion-list"><button v-for="document in suggestions" :key="document.id" @click="suggest(document.title)"><AppIcon name="file" :size="15" /><span>了解 {{ document.title }}</span><AppIcon name="arrow" :size="14" /></button></div></template>
      <div v-else-if="library.loaded" class="ask-no-documents"><AppIcon name="library" :size="18" /><span>先收录一份资料，就能开始有依据的问答。</span><RouterLink to="/">添加资料<AppIcon name="arrow" :size="14" /></RouterLink></div>
      <div class="ask-principles"><span><AppIcon name="check" :size="14" />只依据你的资料</span><i /><span><AppIcon name="quote" :size="14" />出处可核验</span><i /><span><AppIcon name="info" :size="14" />依据不足时明确说明</span></div>
    </div>

    <section v-if="qa.loading" class="answer-loading" :class="{ 'is-background': qa.historyMode }" role="status" aria-live="polite"><span class="loading-orbit"><AppIcon name="spark" :size="23" /></span><div><h2>{{ qa.historyMode ? '另一条回答正在生成…' : '正在查找资料，整理回答…' }}</h2><p class="pending-question">{{ qa.pendingQuestion }}</p><p>已等待 {{ seconds }} 秒<span v-if="seconds >= 20"> · 模型仍在处理，请稍候</span></p></div><div v-if="!qa.historyMode" class="loading-lines" aria-hidden="true"><span class="skeleton" /><span class="skeleton" /><span class="skeleton" /></div></section>
    <section v-if="qa.previewText && !qa.historyMode" class="stream-preview" aria-label="正在生成的回答" :aria-busy="qa.loading">
      <p class="eyebrow">{{ qa.loading ? '正在生成，完成后核验出处并保存' : '回答未完成，以下内容仅供预览' }}</p>
      <p v-if="qa.firstTextMs !== null" class="history-description">首字 {{ (qa.firstTextMs / 1000).toFixed(1) }} 秒</p>
      <div class="stream-preview-text">{{ qa.previewText }}</div>
    </section>
    <div v-if="qa.latestSavedId !== null" class="notice notice-success question-saved-notice" role="status"><AppIcon name="check" :size="17" /><span>新回答已保存到历史。</span><button class="text-button" @click="qa.openHistory(qa.latestSavedId)">查看新回答</button></div>
    <section v-if="qa.detailLoading" class="history-detail-loading" role="status"><AppIcon name="clock" :size="18" /><span>正在读取选中的历史回答…</span></section>
    <div v-if="qa.detailError" class="notice notice-error history-detail-error" role="alert"><AppIcon name="info" :size="18" /><span>{{ qa.detailError }}</span><button v-if="qa.selectedHistoryId !== null" class="text-button" @click="qa.openHistory(qa.selectedHistoryId)">重新读取</button></div>
    <AnswerView v-if="qa.result" :key="qa.selectedHistoryId ?? 'current'" :result="qa.result" :question="qa.answeredQuestion" :elapsed-ms="qa.elapsedMs" :model="qa.usedModel" :created-at="qa.result.created_at" :stale="qa.sourceChanged" :history="qa.historyMode" :retry-disabled="qa.loading" @retry="retry" />
  </section>
  <aside class="question-history" aria-labelledby="question-history-heading" :aria-busy="qa.historyLoading">
    <div class="history-heading">
      <h2 id="question-history-heading"><AppIcon name="clock" :size="16" />问答历史<span class="count-badge">{{ qa.historyTotal }}</span></h2>
      <button class="icon-button history-refresh" aria-label="刷新历史" title="刷新历史" :disabled="qa.historyLoading || qa.deletingHistoryId !== null" @click="changeHistoryPage()"><AppIcon name="refresh" :size="15" :class="{ spinning: qa.historyLoading }" /></button>
    </div>
    <p class="history-description">保存过的答案与当时出处</p>
    <div v-if="qa.historyError" class="notice notice-error history-error" role="alert">{{ qa.historyError }}</div>
    <p v-if="qa.historyLoading && !qa.historyItems.length" class="history-empty" role="status">正在读取历史…</p>
    <div v-else-if="!qa.historyItems.length && !qa.historyError" class="history-empty"><AppIcon name="chat" :size="22" /><p>还没有问答记录</p><span>完成一次提问后，答案会保存在这里。</span></div>
    <ol v-if="qa.historyItems.length" class="history-list">
      <li v-for="item in qa.historyItems" :key="item.id" :data-history-id="item.id" :class="{ 'is-selected': qa.selectedHistoryId === item.id }">
        <button class="history-select" :aria-current="qa.selectedHistoryId === item.id ? 'true' : undefined" :disabled="qa.deletingHistoryId === item.id" @click="qa.openHistory(item.id)">
          <span class="history-question">{{ item.question }}</span>
          <span class="history-row-meta"><time :datetime="item.created_at" title="北京时间">{{ item.created_at.replace('T', ' ').slice(0, 16) }}</time><span class="history-status" :class="item.status.toLowerCase()">{{ historyStatusLabels[item.status] }}</span></span>
          <span class="history-model"><span :title="`${item.model.provider} · ${item.model.model}`">{{ item.model.provider }} · {{ item.model.model }}</span><span>{{ (item.elapsed_ms / 1000).toFixed(1) }} 秒</span></span>
        </button>
        <button class="icon-button history-delete" :aria-label="`删除历史：${item.question}`" :disabled="qa.deletingHistoryId !== null" @click="confirmHistoryId = item.id"><AppIcon name="trash" :size="14" /></button>
        <div v-if="confirmHistoryId === item.id" class="history-confirmation">
          <p>删除这条记录及其保存的出处？</p>
          <button class="text-button history-confirm-delete" :disabled="qa.deletingHistoryId !== null" @click="removeHistory(item.id)">{{ qa.deletingHistoryId === item.id ? '正在删除…' : '确认删除' }}</button>
          <button class="text-button" :disabled="qa.deletingHistoryId !== null" @click="confirmHistoryId = null">取消</button>
        </div>
      </li>
    </ol>
    <nav v-if="qa.historyTotal" class="history-pagination" aria-label="问答历史分页">
      <button class="icon-button" aria-label="上一页" :disabled="qa.historyPage === 0 || qa.historyLoading || qa.deletingHistoryId !== null" @click="changeHistoryPage(qa.historyPage - 1)"><AppIcon name="arrowLeft" :size="15" /></button>
      <span>第 {{ qa.historyPage + 1 }} / {{ historyPageCount }} 页</span>
      <button class="icon-button" aria-label="下一页" :disabled="qa.historyPage + 1 >= historyPageCount || qa.historyLoading || qa.deletingHistoryId !== null" @click="changeHistoryPage(qa.historyPage + 1)"><AppIcon name="arrow" :size="15" /></button>
    </nav>
  </aside>
  </div>
</template>

<style scoped>
.stream-preview { min-width: 0; padding: 1.5rem; margin-top: 1rem; border: 1px solid var(--border, #deded6); border-radius: 16px; }
.stream-preview-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
</style>
