<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

import AppIcon from '@/shared/components/AppIcon.vue'
import { useGateStore } from '@/shared/gate'
import { useQuestionStore } from '../store'
import ModelSettings from './ModelSettings.vue'

const qa = useQuestionStore()
const gate = useGateStore()
const input = ref<HTMLTextAreaElement>()
const modelMenu = ref<HTMLDetailsElement>()
const modelSummary = ref<HTMLElement>()
const settingsOpen = ref(false)
const length = computed(() => [...qa.draft].length)
const disabled = computed(() => qa.loading || !qa.draft.trim() || length.value > 2000 || !gate.canAsk)

function submit() {
  if (!disabled.value) void qa.submit()
}

function openSettings() {
  if (modelMenu.value) modelMenu.value.open = false
  settingsOpen.value = true
}

function closeSettings() {
  settingsOpen.value = false
  void nextTick(() => modelSummary.value?.focus({ preventScroll: true }))
}

function shortcut(event: KeyboardEvent) {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault()
    submit()
  }
}

onMounted(() => {
  if (!qa.result && !qa.loading) input.value?.focus({ preventScroll: true })
})
defineExpose({ focus: () => input.value?.focus() })
</script>

<template>
  <form class="question-composer" :class="{ 'is-loading': qa.loading }" @submit.prevent="submit">
    <label for="knowledge-question" class="visually-hidden">向知识库提问</label>
    <textarea id="knowledge-question" ref="input" v-model="qa.draft" rows="3" :disabled="qa.loading" placeholder="有什么问题，想从你的资料里找到答案？" aria-describedby="question-hint" @keydown="shortcut" />
    <div class="composer-toolbar">
      <details ref="modelMenu" class="model-details">
        <summary ref="modelSummary"><AppIcon name="spark" :size="15" /><span>{{ gate.modelName || (gate.checked ? '回答模型未就绪' : '正在读取模型…') }}</span><AppIcon name="down" :size="12" /></summary>
        <div class="model-popover">
          <p class="popover-eyebrow">当前使用的模型</p>
          <div class="model-info-row"><span class="model-symbol"><AppIcon name="spark" :size="18" /></span><div><strong>{{ gate.modelName || '尚未配置' }}</strong><small>用于理解问题、组织答案</small></div><AppIcon v-if="gate.modelName" name="check" :size="17" /></div>
          <div v-if="gate.runtime?.embedding" class="model-info-row"><span class="model-symbol is-secondary"><AppIcon name="search" :size="17" /></span><div><strong>{{ gate.runtime.embedding.model }}</strong><small>用于查找相关资料</small></div></div>
          <button type="button" class="text-button model-configure" :disabled="qa.loading || !gate.runtime?.rag_available" @click="openSettings">配置回答模型<AppIcon name="arrow" :size="14" /></button>
        </div>
      </details>
      <div class="composer-submit-area">
        <span v-if="length > 1800" class="character-count" :class="{ 'field-error': length > 2000 }">{{ length }} / 2000</span>
        <span class="keyboard-hint">Ctrl ↵</span>
        <button class="send-button" type="submit" :disabled="disabled" :aria-label="qa.loading ? '正在回答' : '发送问题'"><AppIcon :name="qa.loading ? 'clock' : 'up'" :size="20" /></button>
      </div>
    </div>
  </form>
  <p id="question-hint" class="composer-hint"><AppIcon name="book" :size="13" /><span>{{ gate.checked && !gate.canAsk ? `${gate.statusLabel}，就绪后即可提问。` : '依据已收录的资料回答，并为你标明出处。' }}</span></p>
  <ModelSettings v-if="settingsOpen" @close="closeSettings" />
</template>

<style scoped>
.model-configure { width: 100%; justify-content: space-between; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--line); font-size: 12px; }
</style>
