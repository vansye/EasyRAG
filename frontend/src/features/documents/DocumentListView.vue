<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AppIcon from '@/shared/components/AppIcon.vue'
import { documentStatus, displayDate } from './model'
import { useDocumentStore } from './store'
import '@/styles/documents.css'

const library = useDocumentStore()
const route = useRoute()
const router = useRouter()
const fileInput = ref<HTMLInputElement>()
const searchInput = ref(library.search)
const dragDepth = ref(0)
let pollingTimer: ReturnType<typeof setTimeout> | undefined
let searchTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false

function schedulePolling() {
  clearTimeout(pollingTimer)
  if (!disposed && library.hasPending) pollingTimer = setTimeout(() => void refresh(true), 2000)
}

async function refresh(quiet = false) {
  clearTimeout(pollingTimer)
  await library.load(quiet)
  schedulePolling()
}

function search() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    library.page = 0
    library.search = searchInput.value
  }, 250)
}

async function receiveFiles(files: FileList | null) {
  if (!files?.length || library.uploading) return
  if (files.length > 1) {
    library.error = '请一次添加一份资料，处理完成后可以继续添加。'
    return
  }
  await library.upload(files[0]!)
  searchInput.value = library.search
  if (fileInput.value) fileInput.value.value = ''
  if (!disposed) void refresh(true)
}

function drop(event: DragEvent) {
  dragDepth.value = 0
  void receiveFiles(event.dataTransfer?.files ?? null)
}

function openDocument(id: number) {
  void router.push({ query: { ...route.query, document: String(id) } })
}

watch(() => [library.search, library.filter, library.page], () => void refresh(), { immediate: true })
watch(() => library.hasPending, schedulePolling)
onUnmounted(() => {
  disposed = true
  clearTimeout(pollingTimer)
  clearTimeout(searchTimer)
})
</script>

<template>
  <section class="library-page page-enter">
    <div class="page-heading">
      <div>
        <p class="eyebrow"><span class="tiny-line" /> 我的知识空间</p>
        <h1>把知识，留在手边<span class="heading-dot">。</span></h1>
        <p class="page-description">收录你的笔记和文档，让每一次提问都有据可循。</p>
      </div>
      <button class="button button-primary" :disabled="!library.canUpload" :title="library.uploadBlockedReason" @click="fileInput?.click()"><AppIcon name="plus" :size="18" />添加资料</button>
    </div>

    <input ref="fileInput" type="file" accept=".md,.txt,text/plain,text/markdown" class="visually-hidden" tabindex="-1" aria-label="选择要上传的资料" :disabled="!library.canUpload" @change="receiveFiles(($event.target as HTMLInputElement).files)" />

    <button class="upload-zone" :class="{ 'is-dragging': dragDepth > 0 && library.canUpload, 'is-uploading': library.uploading }" :disabled="!library.canUpload" @click="fileInput?.click()" @dragover.prevent @dragenter.prevent="dragDepth++" @dragleave.prevent="dragDepth = Math.max(0, dragDepth - 1)" @drop.prevent="drop">
      <span class="upload-symbol"><AppIcon :name="library.uploading ? 'refresh' : 'upload'" :size="23" :class="{ spinning: library.uploading }" /></span>
      <span class="upload-copy">
        <strong>{{ library.uploading ? `正在收录 ${library.uploadName}` : library.uploadBlockedReason ? '稍后继续添加资料' : dragDepth > 0 ? '松开鼠标，添加这份资料' : '把资料拖到这里，或点击上传' }}</strong>
        <span>{{ library.uploading ? '收录后将自动处理，请稍候。' : library.uploadBlockedReason || '从一份笔记开始，慢慢积累你的知识库。' }}</span>
      </span>
      <span class="upload-formats"><span>MD</span><span>TXT</span><small>每份不超过 1 MB</small></span>
    </button>

    <div v-if="library.notice" class="notice notice-success local-notice" role="status">
      <AppIcon name="check" :size="18" /><span>{{ library.notice }}</span>
      <button class="icon-button" aria-label="关闭上传提示" @click="library.notice = ''"><AppIcon name="close" :size="16" /></button>
    </div>
    <div v-if="library.error" class="notice notice-error local-notice" role="alert">
      <AppIcon name="info" :size="18" /><span>{{ library.error }}</span>
      <button class="text-button" :disabled="library.loading" @click="refresh()">重试</button>
    </div>

    <div class="library-toolbar">
      <div class="section-title"><h2>全部资料</h2><span class="count-badge">{{ library.allTotal }}</span></div>
      <div class="library-filters">
        <label class="search-field"><AppIcon name="search" :size="17" /><input v-model="searchInput" type="search" placeholder="搜索资料标题…" aria-label="搜索资料标题" @input="search" /></label>
        <label class="filter-field"><AppIcon name="filter" :size="16" /><select v-model="library.filter" aria-label="筛选资料状态" @change="library.page = 0"><option value="">全部状态</option><option value="INDEXED">可查询</option><option value="FAILED">处理失败</option></select><AppIcon name="down" :size="13" /></label>
      </div>
    </div>

    <div class="document-surface" :aria-busy="library.loading">
      <table v-if="library.items.length > 0 || (library.loading && !library.loaded)" class="document-table">
        <thead><tr><th scope="col">资料名称</th><th scope="col" class="type-column">来源</th><th scope="col">可用状态</th><th scope="col" class="date-column">最近更新</th><th scope="col"><span class="visually-hidden">操作</span></th></tr></thead>
        <tbody v-if="library.loading && !library.loaded" aria-label="正在加载资料">
          <tr v-for="row in 4" :key="row" class="skeleton-row"><td><span class="skeleton skeleton-name" /></td><td class="type-column"><span class="skeleton skeleton-small" /></td><td><span class="skeleton skeleton-small" /></td><td class="date-column"><span class="skeleton skeleton-small" /></td><td /></tr>
        </tbody>
        <tbody v-else>
          <tr v-for="document in library.items" :key="document.id">
            <td class="document-name-cell">
              <span class="document-file-icon" :class="{ 'is-web': document.source_type === 'URL' }"><AppIcon :name="document.source_type === 'URL' ? 'link' : 'file'" :size="21" /></span>
              <div class="document-title-group">
                <button class="document-title" @click="openDocument(document.id)">{{ document.title }}</button>
                <span v-if="document.tags.length" class="document-tags"><span v-for="tag in document.tags.slice(0, 3)" :key="tag">{{ tag }}</span></span>
                <span v-else class="document-subtitle">{{ document.source_type === 'URL' ? '网页收录' : '上传的笔记与文档' }}</span>
              </div>
            </td>
            <td class="type-column"><span class="source-type">{{ document.source_type === 'URL' ? '网页' : '文档' }}</span></td>
            <td><span class="status-badge" :class="documentStatus(document.index_status).tone"><AppIcon :name="documentStatus(document.index_status).icon" :size="13" />{{ documentStatus(document.index_status).label }}</span></td>
            <td class="date-column"><time :datetime="document.updated_at" :title="new Date(document.updated_at).toLocaleString('zh-CN')">{{ displayDate(document.updated_at) }}</time></td>
            <td class="row-action-cell"><button class="icon-button row-open" :aria-label="`查看${document.title}`" @click="openDocument(document.id)"><AppIcon name="chevron" :size="18" /></button></td>
          </tr>
        </tbody>
      </table>
      <div v-else-if="library.loaded && !library.error" class="library-empty">
        <div class="empty-library-art" aria-hidden="true"><div class="art-page art-page-back" /><div class="art-page art-page-front"><AppIcon name="file" :size="35" /><i /><i /></div><span class="art-plus">+</span></div>
        <h3>{{ library.search || library.filter ? '没有找到符合条件的资料' : '第一份资料，是一切的开始' }}</h3>
        <p>{{ library.search || library.filter ? '试试其他标题，或切换资料状态。' : '上传一份笔记或文档，把收藏变成随时可用的知识。' }}</p>
        <button v-if="library.search || library.filter" class="button button-outlined" @click="library.search = ''; searchInput = ''; library.filter = ''">清除筛选</button>
        <button v-else class="button button-primary" :disabled="!library.canUpload" :title="library.uploadBlockedReason" @click="fileInput?.click()"><AppIcon name="plus" :size="17" />添加第一份资料</button>
      </div>
      <div v-else-if="library.error && !library.items.length" class="library-empty"><AppIcon name="info" :size="32" /><h3>暂时无法加载资料</h3><p>已有资料不会丢失，连接恢复后可以继续浏览。</p><button class="button button-outlined" @click="refresh()">重新加载</button></div>

      <div v-if="library.items.length" class="table-footer">
        <span>{{ library.search || library.filter ? '符合条件的资料' : '共收录' }} <strong>{{ library.total }}</strong> 份<span v-if="library.hasPending" class="polling-note"><span class="status-dot is-pulsing" />正在自动更新状态</span></span>
        <div class="pagination" aria-label="资料分页"><button class="icon-button" :disabled="library.page === 0 || library.loading" aria-label="上一页" @click="library.page--"><AppIcon name="arrowLeft" :size="16" /></button><span>{{ library.page + 1 }} <span>/</span> {{ library.pages }}</span><button class="icon-button" :disabled="library.page + 1 >= library.pages || library.loading" aria-label="下一页" @click="library.page++"><AppIcon name="arrow" :size="16" /></button></div>
      </div>
    </div>

    <div class="library-bottom-note"><span><AppIcon name="refresh" :size="15" />资料更新后，下一次回答会使用新内容。</span><RouterLink to="/ask">去问一个问题<AppIcon name="arrow" :size="15" /></RouterLink></div>
  </section>
</template>
