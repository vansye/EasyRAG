import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const baseUrl = process.env.EASYRAG_PREVIEW_URL || 'http://127.0.0.1:5173'
const artifacts = new URL('../.verification/screenshots/', import.meta.url)
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
page.setDefaultTimeout(10000)
const pageErrors = []
page.on('pageerror', (error) => pageErrors.push(error.message))
const initial = { configured: true, provider: 'openai', model: 'initial-model', base_url: 'https://models.example.test/v1', api_key_configured: true, source: 'environment' }
let config = { ...initial }
let failNextSave = false
const writes = []

await page.route('**/health', (route) => route.fulfill({ json: { status: 'UP', db: { status: 'UP' } } }))
await page.route(/\/api\//, async (route) => {
  const request = route.request()
  const path = new URL(request.url()).pathname
  if (!path.startsWith('/api/')) return route.continue()
  if (path === '/api/runtime') return route.fulfill({ json: { state: 'READY', rag_available: true, llm: { configured: config.configured, provider: config.provider, model: config.model }, embedding: { provider: 'ollama', model: 'bge-m3', dim: 1024 } } })
  if (path === '/api/documents') return route.fulfill({ json: { total: 0, items: [] } })
  if (path === '/api/model-config') {
    if (request.method() === 'PUT') {
      const payload = request.postDataJSON()
      writes.push(payload)
      if (failNextSave) {
        failNextSave = false
        return route.fulfill({ status: 400, json: { error: '保存失败，请检查模型配置。' } })
      }
      config = { ...config, provider: payload.provider, model: payload.model, base_url: payload.base_url, source: 'local' }
    } else if (request.method() === 'DELETE') config = { ...initial }
    return route.fulfill({ json: config })
  }
  return route.fulfill({ status: 404, json: { error: 'Unspecified test endpoint' } })
})

async function openSettings() {
  await page.locator('.model-details > summary').click()
  await page.getByRole('button', { name: '配置回答模型', exact: true }).click()
  await page.getByRole('dialog', { name: '回答模型配置', exact: true }).waitFor()
  await page.getByLabel('模型名称', { exact: true }).waitFor()
}

try {
  await page.goto(baseUrl + '/ask')
  await page.getByText('initial-model', { exact: true }).first().waitFor()
  await openSettings()
  assert.equal(await page.getByLabel('模型名称', { exact: true }).inputValue(), 'initial-model')
  assert.equal(await page.getByLabel('API Key', { exact: true }).inputValue(), '')
  await page.screenshot({ path: fileURLToPath(new URL('test-model-settings.png', artifacts)), fullPage: true })
  await page.getByLabel('模型名称', { exact: true }).fill('selected-model')
  await page.getByRole('button', { name: '保存并使用', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(writes[0].model, 'selected-model')
  assert.equal(writes[0].api_key || '', '')
  await page.locator('.model-details > summary').getByText('selected-model', { exact: true }).waitFor()

  await openSettings()
  await page.getByLabel('接口地址', { exact: true }).fill('https://new.example.test/v1')
  assert.equal(await page.getByLabel('API Key', { exact: true }).getAttribute('required'), '')
  await page.getByLabel('API Key', { exact: true }).fill('temporary-browser-test-key')
  failNextSave = true
  await page.getByRole('button', { name: '保存并使用', exact: true }).click()
  await page.getByText('保存失败，请检查模型配置。', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('模型名称', { exact: true }).inputValue(), 'selected-model')
  await page.getByRole('button', { name: '保存并使用', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(writes.at(-1).api_key, 'temporary-browser-test-key')

  await page.reload()
  await page.locator('.model-details > summary').getByText('selected-model', { exact: true }).waitFor()
  await openSettings()
  assert.equal(await page.getByLabel('接口地址', { exact: true }).inputValue(), 'https://new.example.test/v1')
  assert.equal(await page.getByLabel('API Key', { exact: true }).inputValue(), '')
  assert.equal(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]).includes('temporary-browser-test-key')), false)
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.equal(await page.evaluate(() => document.activeElement?.matches('.model-details > summary')), true)

  await page.setViewportSize({ width: 390, height: 844 })
  await openSettings()
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
  assert.ok(await page.getByRole('dialog').evaluate((dialog) => dialog.scrollWidth <= dialog.clientWidth))
  await page.screenshot({ path: fileURLToPath(new URL('test-model-settings-mobile.png', artifacts)), fullPage: true })
  await page.getByRole('button', { name: '恢复启动配置', exact: true }).click()
  assert.equal(config.model, 'selected-model', 'reset requires explicit confirmation')
  await page.getByRole('button', { name: '确认恢复', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  await page.locator('.model-details > summary').getByText('initial-model', { exact: true }).waitFor()
  assert.deepEqual(pageErrors, [])
  console.log('Model settings browser smoke passed: save, retry, key handling, reload, reset, focus, 390 px.')
} finally {
  await browser.close()
}
