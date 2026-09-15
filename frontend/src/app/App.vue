<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import DocumentDrawer from '@/features/documents/components/DocumentDrawer.vue'
import AppIcon from '@/shared/components/AppIcon.vue'
import { useGateStore } from '@/shared/gate'

const gate = useGateStore()
const route = useRoute()
const router = useRouter()
const documentId = computed(() => positiveId(route.query.document))
const chunkId = computed(() => positiveId(route.query.chunk))
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false

function positiveId(value: unknown): number | null {
  const id = typeof value === 'string' ? Number(value) : NaN
  return Number.isInteger(id) && id > 0 ? id : null
}

function closeDocument() {
  const query = { ...route.query }
  delete query.document
  delete query.chunk
  void router.replace({ query })
}

async function refreshRuntime() {
  await gate.refresh()
  if (!disposed) refreshTimer = setTimeout(refreshRuntime, gate.visible ? 2000 : 12000)
}

onMounted(() => void refreshRuntime())
onUnmounted(() => {
  disposed = true
  clearTimeout(refreshTimer)
})
</script>

<template>
  <a class="skip-link" href="#main-content">跳转到主要内容</a>
  <div class="app-shell">
    <header class="app-header">
      <RouterLink to="/" class="brand" aria-label="EasyRAG 首页">
        <span class="brand-mark"><AppIcon name="book" :size="23" /></span>
        <span class="brand-word">Easy<span>RAG</span></span>
      </RouterLink>
      <nav class="app-nav" aria-label="主导航">
        <RouterLink to="/" class="nav-link"><AppIcon name="library" :size="17" />资料库</RouterLink>
        <RouterLink to="/ask" class="nav-link"><AppIcon name="chat" :size="17" />知识问答</RouterLink>
      </nav>
      <button class="connection-state" :class="{ 'is-offline': gate.checked && !gate.connected, 'is-waiting': gate.visible }" :disabled="gate.refreshing" title="刷新服务状态" @click="gate.refresh()">
        <span class="status-dot" :class="{ 'is-pulsing': !gate.checked }" />
        <span>{{ gate.statusLabel }}</span>
      </button>
    </header>

    <div class="app-notices" aria-live="polite">
      <div v-if="gate.checked && !gate.connected" class="notice notice-error">
        <AppIcon name="info" :size="18" />
        <span>{{ gate.connectionError }}</span>
        <button class="text-button" :disabled="gate.refreshing" @click="gate.refresh()">重新连接</button>
      </div>
      <div v-else-if="gate.visible" class="notice notice-warm">
        <AppIcon :name="gate.needsConfirmation ? 'info' : 'clock'" :size="18" />
        <div class="notice-message">
          <span v-if="gate.needsConfirmation">服务已重新启动，请确认知识库就绪后继续提问。你仍可浏览资料。</span>
          <span v-else>正在处理资料，完成后即可继续提问。你仍可浏览已有内容。</span>
          <span v-if="gate.recoveryError" class="field-error">{{ gate.recoveryError }}</span>
        </div>
        <button v-if="gate.needsConfirmation" class="button button-small button-outlined" :disabled="gate.recovering || !gate.runtime?.rag_available" @click="gate.recover()">
          {{ gate.recovering ? '确认中…' : '确认就绪' }}
        </button>
      </div>
      <div v-else-if="gate.connected && !gate.runtime?.rag_available" class="notice notice-warm">
        <AppIcon name="info" :size="18" /><span>问答服务暂未连接。你可以继续浏览资料，连接恢复后即可添加新内容。</span>
        <button class="text-button" :disabled="gate.refreshing" @click="gate.refresh()">重试连接</button>
      </div>
      <div v-if="gate.notice" class="notice notice-success">
        <AppIcon name="check" :size="18" /><span>{{ gate.notice }}</span>
        <button class="icon-button" aria-label="关闭提示" @click="gate.notice = ''"><AppIcon name="close" :size="16" /></button>
      </div>
    </div>

    <main id="main-content" class="app-main" tabindex="-1">
      <RouterView />
    </main>

    <footer class="app-footer">
      <span><span class="footer-brand">EasyRAG</span> <span class="footer-divider">/</span> 让知识有据可循</span>
      <span class="footer-note"><AppIcon name="quote" :size="12" /> 每一个答案，都有出处。</span>
    </footer>
  </div>
  <DocumentDrawer :document-id="documentId" :chunk-id="chunkId" @close="closeDocument" />
</template>
