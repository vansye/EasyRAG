<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'

import AppIcon from '@/shared/components/AppIcon.vue'
import { useGateStore } from '@/shared/gate'
import { canKeepModelKey, getModelConfig, modelDefaultUrls, resetModelConfig, saveModelConfig } from '../model-config'
import type { ModelConfig, ModelProvider } from '../model-config'
import { useQuestionStore } from '../store'

const emit = defineEmits<{ close: [] }>()
const gate = useGateStore()
const qa = useQuestionStore()
const dialog = ref<HTMLDialogElement>()
const modelInput = ref<HTMLInputElement>()
const current = ref<ModelConfig | null>(null)
const form = reactive({ provider: 'openai' as ModelProvider, model: '', base_url: modelDefaultUrls.openai })
const apiKey = ref('')
const loading = ref(true)
const saving = ref(false)
const resetting = ref(false)
const confirmReset = ref(false)
const error = ref('')
const busy = computed(() => loading.value || saving.value || resetting.value || qa.loading)
const keepKey = computed(() => canKeepModelKey(current.value, form.provider, form.base_url))

async function read() {
  loading.value = true
  error.value = ''
  try {
    current.value = await getModelConfig()
    form.provider = current.value.provider
    form.model = current.value.model
    form.base_url = current.value.base_url
    apiKey.value = ''
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '暂时无法读取模型配置，请重试。'
  } finally {
    loading.value = false
  }
}

function changeProvider() {
  if (Object.values(modelDefaultUrls).includes(form.base_url)) form.base_url = modelDefaultUrls[form.provider]
  apiKey.value = ''
}

function close() {
  if (saving.value || resetting.value) return
  apiKey.value = ''
  dialog.value?.close()
  emit('close')
}

async function apply(config: ModelConfig, notice: string) {
  apiKey.value = ''
  await gate.refresh()
  if (gate.runtime) gate.runtime.llm = { configured: config.configured, provider: config.provider, model: config.model || null }
  gate.notice = notice
  saving.value = false
  resetting.value = false
  close()
}

async function save() {
  if (busy.value || !current.value) return
  error.value = ''
  try {
    const address = new URL(form.base_url.trim())
    if (!['http:', 'https:'].includes(address.protocol) || address.username || address.password || address.search || address.hash) throw new Error('invalid address')
  } catch {
    error.value = '接口地址须使用 http 或 https，密钥请填在单独的 API Key 中，地址不要附带查询参数。'
    return
  }
  saving.value = true
  try {
    const result = await saveModelConfig({ ...form, model: form.model.trim(), base_url: form.base_url.trim(), api_key: apiKey.value.trim() })
    await apply(result, `回答模型已保存为 ${result.model}，下一次提问生效。`)
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '模型配置未能保存，请重试。'
  } finally {
    saving.value = false
  }
}

async function reset() {
  if (busy.value) return
  resetting.value = true
  error.value = ''
  try {
    await apply(await resetModelConfig(), '已恢复启动时的回答模型配置。')
  } catch (failure) {
    error.value = failure instanceof Error ? failure.message : '恢复配置失败，请重试。'
  } finally {
    resetting.value = false
  }
}

onMounted(async () => {
  dialog.value?.showModal()
  await read()
  await nextTick()
  modelInput.value?.focus({ preventScroll: true })
})
onUnmounted(() => { apiKey.value = '' })
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="model-settings" aria-labelledby="model-settings-title" @cancel.prevent="close" @click="($event.target === dialog) && close()">
      <header class="settings-header">
        <span class="settings-symbol"><AppIcon name="spark" :size="23" /></span>
        <div><p class="settings-eyebrow">让答案，用你选择的模型生成</p><h2 id="model-settings-title">回答模型配置</h2></div>
        <button class="icon-button" type="button" aria-label="关闭模型配置" :disabled="saving || resetting" @click="close"><AppIcon name="close" :size="19" /></button>
      </header>
      <p class="settings-intro">保存后，从下一次提问开始使用。已有回答会保留。</p>

      <p v-if="loading" class="settings-loading" role="status">正在读取当前配置…</p>
      <div v-if="error" class="settings-error" role="alert"><AppIcon name="info" :size="17" /><span>{{ error }}</span><button v-if="!current" class="text-button" :disabled="busy" @click="read">重试</button></div>

      <form v-if="current" id="model-settings-form" @submit.prevent="save">
        <fieldset :disabled="busy" class="settings-fields">
          <div class="settings-field-pair">
            <div class="settings-field"><label for="model-provider">服务类型</label><select id="model-provider" v-model="form.provider" @change="changeProvider"><option value="openai">OpenAI 兼容</option><option value="deepseek">DeepSeek</option></select></div>
            <div class="settings-field"><label for="model-name">模型名称</label><input id="model-name" ref="modelInput" v-model="form.model" required maxlength="200" autocomplete="off" spellcheck="false" placeholder="例如 deepseek-chat" /></div>
          </div>
          <div class="settings-field"><label for="model-address">接口地址</label><input id="model-address" v-model="form.base_url" type="url" required maxlength="2048" autocomplete="off" spellcheck="false" placeholder="https://api.openai.com/v1" aria-describedby="model-address-hint" /><p id="model-address-hint" class="settings-help">填写服务商提供的基础地址。本地 Ollama 可用 http://localhost:11434/v1。</p></div>
          <div class="settings-field"><label for="model-key">API Key <span v-if="keepKey" class="configured-key" aria-hidden="true"><AppIcon name="check" :size="12" />已配置</span></label><input id="model-key" v-model="apiKey" type="password" aria-label="API Key" :required="!keepKey" maxlength="4096" autocomplete="new-password" spellcheck="false" :placeholder="keepKey ? '留空，继续使用已有密钥' : '填写 API Key'" aria-describedby="model-key-hint" /><p id="model-key-hint" class="settings-help">{{ keepKey ? '已有密钥不会回填；如需更换，在此填写新密钥。' : current.api_key_configured ? '服务类型或接口地址已变更，请填写新密钥。' : '填写服务商提供的密钥；本地 Ollama 可填写 ollama。' }}</p></div>
        </fieldset>
        <div class="settings-footnote"><span class="settings-origin">{{ current.source === 'local' ? '自定义配置' : '启动配置' }}</span><span>密钥仅保存在运行服务的机器上。</span></div>
      </form>

      <div v-if="gate.runtime?.embedding" class="settings-embedding"><AppIcon name="search" :size="17" /><div><span>检索模型 <strong>{{ gate.runtime.embedding.model }}</strong></span><p>继续用于查找资料，无需重新处理知识库。</p></div></div>

      <footer class="settings-footer" :class="{ 'is-confirming': confirmReset }">
        <template v-if="confirmReset">
          <p>移除网页保存的配置，恢复服务启动时的配置？</p>
          <div class="settings-actions"><button class="button button-outlined" :disabled="busy" @click="confirmReset = false">取消</button><button class="button button-primary" :disabled="busy" @click="reset">{{ resetting ? '恢复中…' : '确认恢复' }}</button></div>
        </template>
        <template v-else>
          <button v-if="current?.source === 'local' || (!loading && !current)" class="text-button settings-reset" :disabled="busy" @click="confirmReset = true">恢复启动配置</button><span v-else />
          <div class="settings-actions"><button class="button button-outlined" :disabled="saving || resetting" @click="close">取消</button><button class="button button-primary" type="submit" form="model-settings-form" :disabled="busy || !current">{{ saving ? '保存中…' : '保存并使用' }}</button></div>
        </template>
      </footer>
    </dialog>
  </Teleport>
</template>

<style scoped>
.model-settings { width: min(570px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); margin: auto; padding: 27px 28px 23px; border: 1px solid #dbe3d3; border-radius: 16px; background: #fffefa; color: var(--ink); box-shadow: 0 24px 90px #26331d2b; overflow-y: auto; }
.model-settings::backdrop { background: #25321d42; backdrop-filter: blur(3px); }
.settings-header { display: flex; align-items: center; gap: 13px; }
.settings-symbol { display: grid; place-items: center; flex-shrink: 0; width: 45px; height: 45px; border: 1px solid #dde7d3; border-radius: 11px; color: var(--brand); background: #edf3e6; }
.settings-header > div { flex: 1; min-width: 0; }
.settings-header h2 { font-family: var(--heading-font); font-size: 25px; font-weight: 600; line-height: 1.5; }
.settings-eyebrow { color: var(--muted); font-size: 11px; letter-spacing: .5px; margin-bottom: 2px; }
.settings-header .icon-button { align-self: flex-start; margin: -5px -7px 0 0; }
.settings-intro { color: var(--muted); font-size: 12px; line-height: 1.8; margin: 18px 0 23px; }
.settings-fields { border: 0; padding: 0; margin: 0; min-width: 0; }
.settings-field-pair { display: grid; grid-template-columns: 156px minmax(0, 1fr); gap: 14px; }
.settings-field { margin-bottom: 19px; min-width: 0; }
.settings-field label { display: flex; align-items: center; justify-content: space-between; color: #526149; font-size: 12px; font-weight: 500; margin-bottom: 8px; }
.settings-field input, .settings-field select { width: 100%; min-width: 0; height: 42px; padding: 0 12px; border: 1px solid #dce3d5; border-radius: 7px; color: var(--ink); background: #fff; font-family: var(--body-font); font-size: 13px; transition: border-color .15s, box-shadow .15s; }
.settings-field input:focus, .settings-field select:focus { outline: none; border-color: #9caf8d; box-shadow: 0 0 0 3px #a7bc941b; }
.settings-field input::placeholder { color: #8d9786; }
.settings-field input:disabled, .settings-field select:disabled { opacity: .65; }
.settings-help { margin-top: 8px; color: var(--muted); font-size: 11px; line-height: 1.85; overflow-wrap: anywhere; }
.configured-key { display: inline-flex; align-items: center; gap: 3px; color: #778b62; font-size: 11px; font-weight: 400; }
.settings-footnote { display: flex; align-items: center; gap: 9px; color: var(--muted); font-size: 11px; margin: -2px 0 22px; }
.settings-origin { padding: 3px 6px; border: 1px solid #e1e7da; border-radius: 4px; font-size: 10px; white-space: nowrap; }
.settings-embedding { display: flex; align-items: center; gap: 11px; padding: 13px 14px; border: 1px solid #e5e9df; border-radius: 8px; color: #8b947e; background: #f6f8f1; font-size: 11px; }
.settings-embedding strong { font-weight: 500; color: #61714f; margin-left: 9px; overflow-wrap: anywhere; }
.settings-embedding p { margin-top: 5px; color: var(--muted); line-height: 1.7; }
.settings-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 23px; }
.settings-actions { display: flex; align-items: center; gap: 9px; }
.settings-actions .button { min-height: 37px; padding: 8px 15px; font-size: 12px; }
.settings-reset { font-size: 11px; color: var(--muted); }
.settings-footer.is-confirming { flex-wrap: wrap; padding-top: 15px; border-top: 1px solid var(--line); }
.settings-footer.is-confirming > p { width: 100%; color: var(--muted); font-size: 12px; line-height: 1.8; }
.settings-footer.is-confirming .settings-actions { margin-left: auto; }
.settings-error { display: flex; align-items: flex-start; gap: 8px; padding: 12px; margin-bottom: 17px; color: #945744; background: #fbf1eb; border: 1px solid #efddd0; border-radius: 7px; font-size: 12px; line-height: 1.8; }
.settings-error > svg { flex-shrink: 0; margin-top: 3px; }
.settings-error > span { flex: 1; }
.settings-loading { padding: 20px 0; color: var(--muted); font-size: 13px; }
@media (max-width: 520px) {
  .model-settings { padding: 22px 19px 20px; border-radius: 13px; }
  .settings-header { gap: 10px; }
  .settings-header h2 { font-size: 23px; }
  .settings-eyebrow { font-size: 10px; letter-spacing: 0; }
  .settings-field-pair { grid-template-columns: 1fr; gap: 0; }
  .settings-intro { margin-top: 16px; }
  .settings-field { margin-bottom: 16px; }
  .settings-field input, .settings-field select { font-size: 16px; }
  .settings-footnote { flex-wrap: wrap; line-height: 1.7; }
  .settings-footer { gap: 7px; }
  .settings-actions { gap: 6px; }
  .settings-actions .button { padding: 8px 11px; }
}
</style>
