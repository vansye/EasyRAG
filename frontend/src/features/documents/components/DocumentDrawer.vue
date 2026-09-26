<script setup lang="ts">
import { Lexer } from 'marked'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import AnswerBody from '@/features/qa/components/AnswerBody'
import { useQuestionStore } from '@/features/qa/store'
import type { ChunkRow, DocumentDetail } from '@/shared/api/types'
import AppIcon from '@/shared/components/AppIcon.vue'
import { useGateStore } from '@/shared/gate'
import { deleteDocument, getChunks, getDocument, reindexDocument, updateDocumentContent } from '../api'
import { documentStatus, displayDate, previewSource } from '../model'
import { useDocumentStore } from '../store'
import '@/styles/drawer.css'
import '@/styles/qa.css'

const props = defineProps<{ documentId: number | null; chunkId: number | null }>()
const emit = defineEmits<{ close: [] }>()
const router = useRouter()
const gate = useGateStore()
const library = useDocumentStore()
const qa = useQuestionStore()
const dialog = ref<HTMLDialogElement>()
const detail = ref<DocumentDetail | null>(null)
const chunks = ref<ChunkRow[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const draft = ref('')
const editing = ref(false)
const confirmingDelete = ref(false)
const tabs = ['preview', 'content', 'chunks'] as const
const tab = ref<typeof tabs[number]>('preview')
const feedback = ref('')
let version = 0
let timer: ReturnType<typeof setTimeout> | undefined
let returnFocus: HTMLElement | null = null
const dirty = computed(() => editing.value && draft.value !== detail.value?.content)
const canMutate = computed(() => gate.connected && gate.runtime?.rag_available === true && gate.runtime.state === 'READY' && !qa.loading && !busy.value)
const previewTokens = computed(() => detail.value ? Lexer.lex(previewSource(detail.value.content), { gfm: true, breaks: true }) : [])
const missingHighlight = computed(() => props.chunkId && detail.value && !loading.value && !chunks.value.some((chunk) => chunk.id === props.chunkId))
const stopNavigationGuard = router.beforeEach((to, from) => {
  if (!props.documentId || (to.query.document === from.query.document && to.query.chunk === from.query.chunk)) return
  if (busy.value) return false
  if (dirty.value) return window.confirm('正文还没有保存，确定放弃修改吗？')
})

async function load(quiet = false) {
  const id = props.documentId
  if (!id) return
  const requestVersion = ++version
  clearTimeout(timer)
  if (!quiet) { loading.value = true; error.value = '' }
  try {
    const [document, result] = await Promise.all([getDocument(id), getChunks(id)])
    if (requestVersion !== version) return
    detail.value = document
    chunks.value = result.items
    if (!editing.value) draft.value = document.content
    loading.value = false
    await nextTick()
    if (requestVersion !== version) return
    if (!quiet && props.chunkId) documentElementForChunk(props.chunkId)?.scrollIntoView({ block: 'center' })
    if (['PENDING', 'INDEXING'].includes(document.index_status)) timer = setTimeout(() => void load(true), 2000)
  } catch (failure) {
    if (requestVersion !== version) return
    gate.raise(failure)
    error.value = failure instanceof Error ? failure.message : '资料加载失败，请重试。'
  } finally {
    if (requestVersion === version) loading.value = false
  }
}

function documentElementForChunk(id: number) { return document.getElementById(`detail-chunk-${id}`) }

function navigateTabs(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const step = event.key === 'ArrowRight' ? 1 : tabs.length - 1
  tab.value = editing.value ? 'content' : event.key === 'Home' ? tabs[0]
    : event.key === 'End' ? tabs[tabs.length - 1] : tabs[(tabs.indexOf(tab.value) + step) % tabs.length]!
  document.getElementById(`${tab.value}-tab`)?.focus()
}

async function startEditing() {
  editing.value = true
  tab.value = 'content'
  error.value = ''
  await nextTick()
  document.getElementById('document-editor')?.focus()
}

function requestClose() {
  if (busy.value) return
  emit('close')
}

async function save() {
  const id = props.documentId
  if (!id || !detail.value || !canMutate.value) return
  if (!draft.value.trim()) { error.value = '资料正文不能为空。'; return }
  if (new TextEncoder().encode(draft.value).length > 1024 * 1024) { error.value = '正文超过 1 MB，请拆分后保存。'; return }
  busy.value = true
  error.value = ''
  try {
    const result = await updateDocumentContent(id, draft.value)
    editing.value = false
    if (result.reindexed) qa.markSourceChanged(id)
    feedback.value = result.reindexed ? '修改已保存，正在重新处理资料。' : '内容没有变化，已保留原有资料。'
    await Promise.all([load(true), library.load(true), gate.refreshAfterChange()])
  } catch (failure) {
    gate.raise(failure)
    error.value = failure instanceof Error ? failure.message : '保存失败，请重试。'
  } finally { busy.value = false }
}

async function remove() {
  const id = props.documentId
  if (!id || !detail.value || !canMutate.value) return
  const title = detail.value.title
  busy.value = true
  error.value = ''
  try {
    await deleteDocument(id)
    qa.markSourceChanged(id)
    gate.notice = `「${title}」已删除，后续回答不再使用这份资料。`
    library.allTotal = Math.max(0, library.allTotal - 1)
    await library.load(true)
    if (library.page >= library.pages) { library.page = library.pages - 1; await library.load(true) }
    await gate.refreshAfterChange()
  } catch (failure) {
    gate.raise(failure)
    error.value = failure instanceof Error ? failure.message : '删除失败，请重试。'
    return
  } finally { busy.value = false }
  emit('close')
}

async function retry() {
  const id = props.documentId
  if (!id || !canMutate.value) return
  busy.value = true
  error.value = ''
  try {
    await reindexDocument(id)
    qa.markSourceChanged(id)
    feedback.value = '已重新提交处理，完成后即可用于回答。'
    await Promise.all([load(true), library.load(true), gate.refreshAfterChange()])
  } catch (failure) {
    gate.raise(failure)
    error.value = failure instanceof Error ? failure.message : '重新处理失败，请重试。'
  } finally { busy.value = false }
}

watch(() => [props.documentId, props.chunkId], async () => {
  ++version
  clearTimeout(timer)
  if (!props.documentId) {
    dialog.value?.close()
    document.body.classList.remove('dialog-open')
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
    return
  }
  if (!dialog.value?.open) returnFocus = document.activeElement as HTMLElement | null
  detail.value = null
  chunks.value = []
  editing.value = false
  confirmingDelete.value = false
  feedback.value = ''
  tab.value = props.chunkId ? 'chunks' : 'preview'
  await nextTick()
  if (!dialog.value?.open) dialog.value?.showModal()
  document.body.classList.add('dialog-open')
  await load()
}, { immediate: true })

onUnmounted(() => {
  stopNavigationGuard()
  ++version
  clearTimeout(timer)
  document.body.classList.remove('dialog-open')
})
</script>

<template>
  <dialog ref="dialog" class="document-dialog" aria-labelledby="document-heading" @cancel.prevent="requestClose" @click="($event.target === dialog) && requestClose()">
    <div class="drawer-shell">
      <header class="drawer-header"><span class="eyebrow"><AppIcon name="file" :size="16" />资料详情</span><button class="icon-button" aria-label="关闭资料详情" :disabled="busy" @click="requestClose"><AppIcon name="close" :size="21" /></button></header>

      <div v-if="loading" class="drawer-loading"><span class="skeleton skeleton-heading" /><span v-for="line in 6" :key="line" class="skeleton skeleton-paragraph" /></div>
      <template v-else-if="detail">
        <div class="drawer-title-area"><h2 id="document-heading">{{ detail.title }}</h2><div class="drawer-meta"><span class="status-badge" :class="documentStatus(detail.index_status).tone"><AppIcon :name="documentStatus(detail.index_status).icon" :size="13" />{{ documentStatus(detail.index_status).label }}</span><span>{{ detail.source_type === 'URL' ? '网页收录' : '上传文档' }}</span><span>更新于 {{ displayDate(detail.updated_at) }}</span></div><div v-if="detail.tags.length" class="drawer-tags"><span v-for="tag in detail.tags" :key="tag">{{ tag }}</span></div></div>

        <div v-if="feedback" class="notice notice-success drawer-notice" role="status"><AppIcon name="check" :size="17" /><span>{{ feedback }}</span></div>
        <div v-if="detail.index_status === 'FAILED'" class="notice notice-error drawer-notice"><AppIcon name="info" :size="17" /><div class="notice-message"><strong>这份资料尚未处理完成</strong><span>{{ detail.index_error || '请确认服务可用后，重新处理这份资料。' }}</span></div><button class="text-button" :disabled="!canMutate" @click="retry">重新处理</button></div>
        <div v-if="missingHighlight" class="notice notice-warm drawer-notice"><AppIcon name="info" :size="17" /><span>引用的片段已发生变化，下方展示这份资料的当前内容。</span></div>

        <div class="drawer-tabs" role="tablist" aria-label="资料内容"><button id="preview-tab" role="tab" :aria-selected="tab === 'preview'" :tabindex="tab === 'preview' ? 0 : -1" aria-controls="document-preview" :class="{ active: tab === 'preview' }" :disabled="editing" @click="tab = 'preview'" @keydown="navigateTabs">预览</button><button id="content-tab" role="tab" :aria-selected="tab === 'content'" :tabindex="tab === 'content' ? 0 : -1" aria-controls="document-content" :class="{ active: tab === 'content' }" @click="tab = 'content'" @keydown="navigateTabs">原文</button><button id="chunks-tab" role="tab" :aria-selected="tab === 'chunks'" :tabindex="tab === 'chunks' ? 0 : -1" aria-controls="document-chunks" :class="{ active: tab === 'chunks' }" :disabled="editing" @click="tab = 'chunks'" @keydown="navigateTabs">引用片段<span>{{ chunks.length }}</span></button><button v-if="!editing" class="text-button drawer-edit" :disabled="!canMutate" @click="startEditing"><AppIcon name="edit" :size="14" />修改正文</button><span v-else class="editing-indicator">编辑中</span></div>

        <div class="drawer-scroll-area">
          <section v-show="tab === 'preview'" id="document-preview" role="tabpanel" aria-labelledby="preview-tab"><AnswerBody :tokens="previewTokens" :citations="[]" /></section>
          <section v-show="tab === 'content'" id="document-content" role="tabpanel" aria-labelledby="content-tab">
            <template v-if="editing"><label class="visually-hidden" for="document-editor">编辑资料正文</label><textarea id="document-editor" v-model="draft" class="document-editor" spellcheck="false" :disabled="busy" /><p class="editor-hint">保存后会重新处理资料，新的回答将使用更新后的内容。</p></template>
            <pre v-else class="original-content">{{ detail.content }}</pre>
          </section>
          <section v-show="tab === 'chunks'" id="document-chunks" role="tabpanel" aria-labelledby="chunks-tab" class="chunk-list">
            <article v-for="chunk in chunks" :id="`detail-chunk-${chunk.id}`" :key="chunk.id" class="chunk-card" :class="{ 'is-highlighted': chunk.id === chunkId }"><div class="chunk-heading"><span>片段 {{ chunk.seq + 1 }}</span><span v-if="chunk.id === chunkId" class="chunk-selected-label"><AppIcon name="quote" :size="12" />当前引用</span></div><h3 v-if="chunk.heading_path">{{ chunk.heading_path }}</h3><p>{{ chunk.text }}</p></article>
            <div v-if="!chunks.length" class="drawer-empty"><AppIcon name="clock" :size="28" /><p>这份资料还没有可用的引用片段。</p><span>处理完成后，片段会显示在这里。</span><button v-if="detail.index_status === 'PENDING'" class="button button-outlined button-small" :disabled="!canMutate" @click="retry">重新提交处理</button></div>
          </section>
        </div>

        <div v-if="error" class="notice notice-error drawer-notice" role="alert"><AppIcon name="info" :size="17" /><span>{{ error }}</span></div>
        <div v-if="confirmingDelete" class="delete-confirmation"><div><strong>确定删除这份资料？</strong><p>删除后将不再用于回答，此操作无法撤销。</p></div><div><button class="button button-small button-outlined" :disabled="busy" @click="confirmingDelete = false">取消</button><button class="button button-small button-danger" :disabled="!canMutate" @click="remove">{{ busy ? '删除中…' : '确认删除' }}</button></div></div>
        <footer v-else class="drawer-footer"><div v-if="!editing"><button class="text-button delete-button" :disabled="!canMutate" @click="confirmingDelete = true"><AppIcon name="trash" :size="15" />删除资料</button><button v-if="detail.index_status === 'INDEXED'" class="text-button" :disabled="!canMutate" @click="retry"><AppIcon name="refresh" :size="14" />重新处理</button></div><span v-else class="editor-footer-note">{{ dirty ? '有尚未保存的修改' : '正文未修改' }}</span><div v-if="editing"><button class="button button-outlined button-small" :disabled="busy" @click="editing = false; draft = detail.content; error = ''">取消</button><button class="button button-primary button-small" :disabled="!canMutate || !dirty" @click="save">{{ busy ? '保存中…' : '保存修改' }}</button></div><button v-else class="button button-outlined button-small" :disabled="busy" @click="requestClose">完成</button></footer>
      </template>
      <div v-else class="drawer-empty drawer-failure" role="alert"><AppIcon name="info" :size="32" /><h2 id="document-heading">暂时无法打开资料</h2><p>{{ error }}</p><button class="button button-outlined" @click="load()">重新加载</button></div>
    </div>
  </dialog>
</template>
