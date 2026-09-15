import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { setTimeout as pause } from 'node:timers/promises'
import { chromium } from 'playwright'

// Uses the running local services. Only the uniquely named document created here is changed.
const baseUrl = process.env.EASYRAG_PREVIEW_URL || 'http://127.0.0.1:5173'
const artifacts = new URL('../.verification/screenshots/', import.meta.url)
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
const page = await context.newPage()
page.setDefaultTimeout(15000)
const pageErrors = []
page.on('pageerror', (error) => pageErrors.push(error.message))
const report = { startedAt: new Date().toISOString(), steps: [], queries: [], model: '', originalDocuments: 0 }
const identity = randomUUID().slice(0, 8)
const title = `前端联调临时资料-${identity}`
const question = `青岚阅读计划-${identity} 的活动时间、地点和报名口令分别是什么？`
const content = `---\ntitle: ${title}\ntags: [临时验收]\n---\n\n# 青岚阅读计划-${identity}\n\n这是一份仅用于验证知识库界面的临时资料。\n\n## 活动安排\n\n青岚阅读计划-${identity} 的活动时间是每周三晚上 19:30，地点是北窗阅览室。报名口令是白鹭3718。参与者通过报名口令登记后，可以参加本次阅读交流。\n\n## 阅读方式\n\n每次活动先用二十分钟安静阅读，再由参与者分享一段笔记，并记录值得继续讨论的问题。活动结束后整理笔记，供下一次阅读使用。\n`
const updatedContent = content.replace('每周三晚上 19:30', '每周六上午 09:00').replace('北窗阅览室', '南庭会议室').replaceAll('白鹭3718', '蓝舟8642')
let createdId = null
let deleted = false
let restoreEnvironmentModel = false

function done(step) { report.steps.push(step); console.log(`PASS: ${step}`) }
async function json(path) {
  const response = await context.request.get(`${baseUrl}${path}`)
  assert.equal(response.status(), 200, `GET ${path}`)
  return response.json()
}
async function waitForReady() {
  const until = Date.now() + 120000
  while (Date.now() < until) {
    const runtime = await json('/api/runtime')
    if (runtime.state === 'READY') return
    assert.notEqual(runtime.state, 'RECOVERY_REQUIRED', 'operation requires recovery')
    await pause(1000)
  }
  throw new Error('service did not become READY within 120 seconds')
}
async function waitForIndexed() {
  const until = Date.now() + 120000
  while (Date.now() < until) {
    const document = await json(`/api/documents/${createdId}`)
    assert.notEqual(document.index_status, 'FAILED', document.index_error || 'indexing failed')
    if (document.index_status === 'INDEXED') { await waitForReady(); return document }
    await pause(1000)
  }
  throw new Error('document did not finish indexing within 120 seconds')
}
async function screenshot(name, fullPage = true) {
  if (fullPage) await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: fileURLToPath(new URL(name, artifacts)), fullPage, animations: 'disabled' })
}
async function syncRuntime() {
  await waitForReady()
  await page.getByTitle('刷新服务状态').click()
  await page.getByRole('button', { name: '服务已连接', exact: true }).waitFor()
}
async function ask() {
  await page.getByRole('textbox', { name: '向知识库提问' }).fill(question)
  const pending = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/questions' && response.request().method() === 'POST', { timeout: 240000 })
  const startedAt = Date.now()
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  const response = await pending
  const result = await response.json()
  report.queries.push({ status: response.status(), elapsedMs: Date.now() - startedAt, response: result })
  assert.equal(response.status(), 200, `question failed: ${result.error || response.status()}`)
  await page.locator('.answer-paper').waitFor()
  await syncRuntime()
  return result
}
async function originalSnapshot(ids) {
  return Promise.all(ids.map(async (id) => {
    const document = await json(`/api/documents/${id}`)
    const chunks = await json(`/api/documents/${id}/chunks`)
    return { id, hash: createHash('sha256').update(JSON.stringify({ document, chunks })).digest('hex') }
  }))
}

try {
  await page.goto(baseUrl)
  await page.waitForLoadState('networkidle')
  const runtime = await json('/api/runtime')
  report.model = runtime.llm?.model || ''
  assert.equal(runtime.rag_available, true)
  assert.equal(runtime.llm?.configured, true)
  if (runtime.state === 'RECOVERY_REQUIRED') {
    // Explicit UI action for this integration check; the application never does this automatically.
    await page.getByRole('button', { name: '确认就绪', exact: true }).click()
  }
  await syncRuntime()
  const originalList = await json('/api/documents?size=100')
  const originalIds = originalList.items.map((document) => document.id).sort((a, b) => a - b)
  const before = await originalSnapshot(originalIds)
  report.originalDocuments = originalList.total
  done('connected to actual Java, Python and model; explicit readiness action')
  await page.locator('.document-table').waitFor()
  const closeNotice = page.getByRole('button', { name: '关闭提示', exact: true })
  if (await closeNotice.count()) await closeNotice.click()
  await screenshot('library-live.png')
  await page.getByRole('link', { name: '知识问答', exact: true }).click()
  await page.getByRole('textbox', { name: '向知识库提问' }).waitFor()
  await screenshot('ask-live.png')

  const initialModel = await json('/api/model-config')
  await page.locator('.model-details > summary').click()
  await page.getByRole('button', { name: '配置回答模型', exact: true }).click()
  await page.getByLabel('模型名称', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('模型名称', { exact: true }).inputValue(), initialModel.model)
  assert.equal(await page.getByLabel('API Key', { exact: true }).inputValue(), '')
  // Save the current destination with a blank key, preserving the user's credentials.
  restoreEnvironmentModel = initialModel.source === 'environment'
  await page.getByRole('button', { name: '保存并使用', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  assert.deepEqual(await json('/api/model-config'), { ...initialModel, source: 'local' })
  await page.reload()
  await page.locator('.model-details > summary').getByText(initialModel.model, { exact: true }).waitFor()
  await page.locator('.model-details > summary').click()
  await page.getByRole('button', { name: '配置回答模型', exact: true }).click()
  await page.getByLabel('模型名称', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('接口地址', { exact: true }).inputValue(), initialModel.base_url)
  assert.equal(await page.getByLabel('API Key', { exact: true }).inputValue(), '')
  await page.getByRole('button', { name: '关闭模型配置', exact: true }).click()
  done('saved the current model through the browser and reloaded it without exposing or replacing its key')

  await page.setViewportSize({ width: 390, height: 844 })
  await screenshot('ask-mobile-live.png')
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.getByRole('link', { name: '资料库', exact: true }).click()
  await page.locator('.document-table').waitFor()
  await screenshot('library-mobile-live.png')
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
  await page.setViewportSize({ width: 1440, height: 1000 })

  const uploadResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/documents' && response.request().method() === 'POST')
  await page.getByLabel('选择要上传的资料').setInputFiles({ name: `${title}.md`, mimeType: 'text/markdown', buffer: Buffer.from(content) })
  const uploaded = await uploadResponse
  assert.equal(uploaded.status(), 201)
  createdId = (await uploaded.json()).id
  assert.ok(Number.isInteger(createdId) && !originalIds.includes(createdId))
  report.testDocumentId = createdId
  console.log(`Created isolated integration document: ${createdId}`)
  await waitForIndexed()
  await syncRuntime()
  done('upload and real embedding completed')

  await page.getByRole('link', { name: '知识问答', exact: true }).click()
  const first = await ask()
  assert.ok(first.sources.some((source) => source.document_id === createdId))
  assert.match(first.answer, /白鹭\s*3718/)
  await screenshot('answer-live.png')
  const citation = page.locator('.source-card').filter({ hasText: title }).first()
  await citation.getByRole('button', { name: '查看原文', exact: true }).click()
  await page.locator('dialog[open] .chunk-card.is-highlighted').waitFor()
  assert.match(await page.locator('.chunk-card.is-highlighted').innerText(), /白鹭3718/)
  await screenshot('drawer-live.png', false)
  done('real model answer and citation open the matching original chunk')

  await page.getByRole('button', { name: '修改正文', exact: true }).click()
  await page.getByRole('textbox', { name: '编辑资料正文' }).fill(updatedContent)
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await page.getByText('修改已保存，正在重新处理资料。', { exact: true }).waitFor()
  await waitForIndexed()
  await page.getByRole('button', { name: '关闭资料详情', exact: true }).click()
  await page.getByText('引用资料已发生变化。这是更新前的回答，请重新提问以获取最新内容。', { exact: true }).waitFor()
  await syncRuntime()
  assert.equal((await json(`/api/documents/${createdId}`)).content, updatedContent)
  done('edit preserves new content and marks the previous answer as stale')

  await citation.getByRole('button', { name: '查看原文', exact: true }).click()
  await page.getByRole('button', { name: '重新处理', exact: true }).click()
  await page.getByText('已重新提交处理，完成后即可用于回答。', { exact: true }).waitFor()
  await waitForIndexed()
  await page.getByRole('button', { name: '关闭资料详情', exact: true }).click()
  await syncRuntime()
  const second = await ask()
  assert.match(second.answer, /蓝舟\s*8642/)
  assert.doesNotMatch(second.answer, /白鹭\s*3718|北窗阅览室/)
  done('explicit reindex and a new answer use only the updated information')

  await page.getByRole('link', { name: '资料库', exact: true }).click()
  await page.getByRole('button', { name: title, exact: true }).click()
  await page.getByRole('button', { name: '删除资料', exact: true }).click()
  assert.equal((await context.request.get(`${baseUrl}/api/documents/${createdId}`)).status(), 200)
  const deleteResponse = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/documents/${createdId}` && response.request().method() === 'DELETE')
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  assert.equal((await deleteResponse).status(), 204)
  deleted = true
  await page.locator('dialog[open]').waitFor({ state: 'hidden' })
  assert.equal((await context.request.get(`${baseUrl}/api/documents/${createdId}`)).status(), 404)
  assert.equal((await context.request.get(`${baseUrl}/api/documents/${createdId}/chunks`)).status(), 404)
  await syncRuntime()
  done('delete requires confirmation and removes the original document and chunks')

  await page.getByRole('link', { name: '知识问答', exact: true }).click()
  const afterDelete = await ask()
  assert.equal(afterDelete.status, 'REFUSED')
  assert.ok(afterDelete.sources.every((source) => source.document_id !== createdId))
  done('asking after deletion returns insufficient evidence, without the deleted source')
  const finalList = await json('/api/documents?size=100')
  assert.equal(finalList.total, originalList.total)
  assert.deepEqual(finalList.items.map((document) => document.id).sort((a, b) => a - b), originalIds)
  assert.deepEqual(await originalSnapshot(originalIds), before)
  assert.deepEqual(pageErrors, [])
  done('all existing documents and chunks unchanged; no browser page errors')
  if (restoreEnvironmentModel) {
    await page.locator('.model-details > summary').click()
    await page.getByRole('button', { name: '配置回答模型', exact: true }).click()
    await page.getByRole('button', { name: '恢复启动配置', exact: true }).click()
    await page.getByRole('button', { name: '确认恢复', exact: true }).click()
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    assert.deepEqual(await json('/api/model-config'), initialModel)
    restoreEnvironmentModel = false
    done('restored the original environment model configuration through the browser')
  }
  report.completedAt = new Date().toISOString()
} catch (failure) {
  report.error = failure.message
  await screenshot('live-failure.png')
  throw failure
} finally {
  if (createdId && !deleted) {
    const state = await json('/api/runtime').catch(() => null)
    if (state?.state === 'READY') {
      const cleanup = await context.request.delete(`${baseUrl}/api/documents/${createdId}`)
      report.cleanupStatus = cleanup.status()
      console.log(`Cleanup of test document ${createdId}: HTTP ${cleanup.status()}`)
    } else {
      console.error(`Test document ${createdId} remains for cleanup; service state: ${state?.state}`)
    }
  }
  if (restoreEnvironmentModel) {
    try {
      const cleanup = await context.request.delete(`${baseUrl}/api/model-config`)
      report.modelCleanupStatus = cleanup.status()
      assert.equal(cleanup.status(), 200, 'restore original environment model')
    } catch (failure) {
      report.modelCleanupError = failure.message
      process.exitCode = 1
    }
  }
  await writeFile(new URL('../.verification/live-report.json', import.meta.url), JSON.stringify(report, null, 2))
  await browser.close()
}
