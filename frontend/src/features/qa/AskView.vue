<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { useDocumentStore } from '@/features/documents/store'
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

onMounted(() => {
  if (!library.loaded) void library.load(true)
})
onUnmounted(() => clearInterval(elapsedTimer))
</script>

<template>
  <section class="ask-page page-enter" :class="{ 'has-answer': qa.result || qa.loading }">
    <div v-if="!qa.result && !qa.loading" class="ask-welcome">
      <div class="ask-emblem" aria-hidden="true"><span class="emblem-orbit" /><span class="emblem-dot" /><AppIcon name="book" :size="31" /><span class="emblem-spark">✦</span></div>
      <p class="eyebrow">你的资料，值得被再次发现</p>
      <h1>让答案，回到你的资料里<span class="heading-dot">。</span></h1>
      <p class="ask-description">问一个问题，找到那些你收藏过、记下过的知识。<br />每一份依据，都可以回到原文。</p>
    </div>
    <div v-else class="ask-page-topline"><span class="eyebrow"><AppIcon name="chat" :size="16" />知识库问答</span><button class="text-button" :disabled="qa.loading" @click="newQuestion"><AppIcon name="plus" :size="16" />新问题</button></div>

    <QuestionBox ref="composer" />

    <div v-if="qa.error" class="notice notice-error question-error" role="alert"><AppIcon name="info" :size="18" /><span>{{ qa.error }}</span><button class="text-button" :disabled="qa.loading || !gate.canAsk" @click="qa.submit()">再试一次</button></div>

    <div v-if="!qa.result && !qa.loading" class="ask-starters">
      <template v-if="suggestions.length"><p>从你的资料开始</p><div class="suggestion-list"><button v-for="document in suggestions" :key="document.id" @click="suggest(document.title)"><AppIcon name="file" :size="15" /><span>了解 {{ document.title }}</span><AppIcon name="arrow" :size="14" /></button></div></template>
      <div v-else-if="library.loaded" class="ask-no-documents"><AppIcon name="library" :size="18" /><span>先收录一份资料，就能开始有依据的问答。</span><RouterLink to="/">添加资料<AppIcon name="arrow" :size="14" /></RouterLink></div>
      <div class="ask-principles"><span><AppIcon name="check" :size="14" />只依据你的资料</span><i /><span><AppIcon name="quote" :size="14" />出处可核验</span><i /><span><AppIcon name="info" :size="14" />依据不足时明确说明</span></div>
    </div>

    <section v-if="qa.loading" class="answer-loading" role="status" aria-live="polite"><span class="loading-orbit"><AppIcon name="spark" :size="23" /></span><div><h2>正在查找资料，整理回答…</h2><p>已等待 {{ seconds }} 秒<span v-if="seconds >= 20"> · 模型仍在处理，请稍候</span></p></div><div class="loading-lines" aria-hidden="true"><span class="skeleton" /><span class="skeleton" /><span class="skeleton" /></div></section>
    <AnswerView v-else-if="qa.result" :result="qa.result" :question="qa.answeredQuestion" :elapsed-ms="qa.elapsedMs" :model="qa.usedModel" :stale="qa.sourceChanged" @retry="retry" />
  </section>
</template>
