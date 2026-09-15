import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const baseUrl = process.env.EASYRAG_PREVIEW_URL || 'http://127.0.0.1:5173'
const artifacts = new URL('../.verification/screenshots/', import.meta.url)
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
page.setDefaultTimeout(12000)
const pageErrors = []
const consoleErrors = []
const interceptedPaths = []
page.on('pageerror', (error) => pageErrors.push(error.message))
page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })

const documents = [
  { id: 1, title: 'Redis 持久化机制', tags: ['数据库', 'Redis'], index_status: 'INDEXED' },
  { id: 2, title: '深入理解数据库事务', tags: ['数据库'], index_status: 'INDEXED' },
  { id: 3, title: 'Transformer 与注意力机制', tags: ['大模型'], index_status: 'INDEXED' },
  { id: 4, title: '把笔记变成可用的知识', tags: ['知识管理'], index_status: 'INDEXED' },
  { id: 5, title: 'Java Web 请求处理流程', tags: ['Java Web'], index_status: 'PENDING' },
  { id: 6, title: '我的学习计划', tags: [], index_status: 'FAILED' },
].map((document) => ({ ...document, source_type: 'UPLOAD', chunk_count: 3, updated_at: '2026-09-14T14:20:00', content: `# ${document.title}\n\n这是一份仅在浏览器测试中使用的资料。` }))
let processingUntil = 0
let revision = 0
let deleted = false
let receivedQuestion = ''
let pendingFirstQuestion = true
let releaseFirstQuestion
const source = (id, documentId, text) => ({ chunk_id: id, document_id: documentId, title: documents.find((document) => document.id === documentId)?.title || '已删除资料', text, heading_path: id === 9 ? '持久化 > RDB' : '事务 > 原子性', byte_start: 0, byte_end: 20 })
const answer = {
  answer: 'RDB 会在指定时间保存数据快照，恢复时使用最近的一份快照 [1]。\n\n事务的原子性则保证一组操作全部完成，或者全部不发生 [2]。',
  status: 'ANSWERED',
  sources: [source(2, 2, '事务的原子性保证全部执行或全部回滚。'), source(9, 1, 'RDB 会在指定时间间隔生成数据集的快照。')],
  trace: [{ round_index: 1, query: 'RDB 快照和事务原子性分别是什么？', decision: 'SUFFICIENT', retrieved: [{ chunk_id: 9, document_id: 1, rank: 1, score: 0.1 }, { chunk_id: 2, document_id: 2, rank: 2, score: 0.2 }] }],
}

await page.route('**/health', (route) => route.fulfill({ json: { status: 'UP', service: 'easyrag-server', db: { status: 'UP', database: 'mysql' } } }))
await page.route(/\/api\//, async (route) => {
  const request = route.request()
  const url = new URL(request.url())
  const path = url.pathname
  if (!path.startsWith('/api/')) return route.continue()
  interceptedPaths.push(path)
  if (path === '/api/runtime') return route.fulfill({ json: { state: Date.now() < processingUntil ? 'MUTATING' : 'READY', rag_available: true, llm: { configured: true, provider: 'openai', model: 'browser-test-model' }, embedding: { provider: 'ollama', model: 'bge-m3', dim: 1024 } } })
  if (path === '/api/questions') {
    receivedQuestion = request.postDataJSON().question
    if (pendingFirstQuestion) {
      pendingFirstQuestion = false
      await new Promise((resolve) => { releaseFirstQuestion = resolve })
    }
    const refused = receivedQuestion.includes('缺少资料')
    const partial = receivedQuestion.includes('部分')
    return route.fulfill({ json: refused ? { ...answer, answer: '知识库中没有找到能回答这个问题的内容。', status: 'REFUSED', sources: [], trace: [{ ...answer.trace[0], decision: 'NONE' }] } : partial ? { ...answer, status: 'PARTIAL', trace: [{ ...answer.trace[0], decision: 'PARTIAL' }] } : answer })
  }
  if (path === '/api/documents' && request.method() === 'GET') {
    const filtered = documents.filter((document) => (!deleted || document.id !== 1) && (!url.searchParams.get('status') || document.index_status === url.searchParams.get('status')) && (!url.searchParams.get('q') || document.title.toLowerCase().includes(url.searchParams.get('q').toLowerCase())))
    const pageIndex = Number(url.searchParams.get('page') || 0)
    const size = Number(url.searchParams.get('size') || 10)
    return route.fulfill({ json: { total: filtered.length, items: filtered.slice(pageIndex * size, (pageIndex + 1) * size) } })
  }
  if (path === '/api/documents' && request.method() === 'POST') {
    documents.push({ id: 10, title: '浏览器上传验证', tags: [], index_status: 'INDEXED', source_type: 'UPLOAD', chunk_count: 1, content: '测试资料', updated_at: '2026-09-14T15:00:00' })
    return route.fulfill({ status: 201, json: documents.at(-1) })
  }
  const match = path.match(/^\/api\/documents\/(\d+)(\/chunks|\/reindex)?$/)
  if (match) {
    const id = Number(match[1])
    const document = documents.find((item) => item.id === id)
    if (!document || (id === 1 && deleted)) return route.fulfill({ status: 404, json: { error: '资料不存在或已删除' } })
    if (request.method() === 'DELETE') { deleted = true; return route.fulfill({ status: 204 }) }
    if (request.method() === 'PUT') {
      document.content = request.postDataJSON().content
      revision++
      processingUntil = Date.now() + 700
      return route.fulfill({ json: { id, index_status: 'PENDING', reindexed: true } })
    }
    if (match[2] === '/chunks') return route.fulfill({ json: { items: [{ id: id === 1 ? (revision ? 19 : 9) : 2, seq: 0, text: document.content, byte_start: 0, byte_end: 20, heading_path: '内容', token_count: 30 }] } })
    return route.fulfill({ json: { ...document, index_status: Date.now() < processingUntil ? 'INDEXING' : document.index_status, index_error: document.index_status === 'FAILED' ? '资料处理未完成，请重试。' : null, source_uri: document.title + '.md', created_at: document.updated_at } })
  }
  return route.fulfill({ status: 404, json: { error: '测试中未定义的接口' } })
})

try {
  await page.goto(baseUrl)
  await page.waitForLoadState('networkidle')
  await page.getByRole('button', { name: 'Redis 持久化机制', exact: true }).waitFor()
  await page.screenshot({ path: fileURLToPath(new URL('test-library.png', artifacts)), fullPage: true })
  await page.getByRole('searchbox', { name: '搜索资料标题' }).fill('Transformer')
  await page.getByRole('button', { name: 'Transformer 与注意力机制', exact: true }).waitFor()
  await page.waitForTimeout(400)
  assert.equal(await page.locator('.document-table tbody tr').count(), 1)
  await page.getByRole('searchbox', { name: '搜索资料标题' }).fill('')
  await page.waitForTimeout(400)

  await page.getByRole('link', { name: '知识问答', exact: true }).click()
  await page.getByRole('textbox', { name: '向知识库提问' }).waitFor()
  await page.screenshot({ path: fileURLToPath(new URL('test-ask.png', artifacts)), fullPage: true })
  await page.getByRole('textbox', { name: '向知识库提问' }).fill('RDB 快照和事务原子性分别是什么？')
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  await page.locator('.answer-loading').waitFor()
  await page.getByRole('link', { name: '资料库', exact: true }).click()
  await page.locator('.upload-zone').waitFor()
  assert.equal(await page.getByRole('button', { name: '添加资料', exact: true }).isDisabled(), true)
  await page.getByText('当前回答完成后，即可继续添加资料。', { exact: true }).waitFor()
  await page.getByRole('link', { name: '知识问答', exact: true }).click()
  await page.locator('.answer-loading').waitFor()
  releaseFirstQuestion()
  await page.locator('.answer-paper').waitFor()
  assert.equal(receivedQuestion, 'RDB 快照和事务原子性分别是什么？')
  await page.getByRole('button', { name: '查看引用 1：Redis 持久化机制', exact: true }).click()
  assert.match(await page.locator('#citation-1').innerText(), /RDB 会在指定时间间隔/)
  await page.locator('#citation-1').getByRole('button', { name: '查看原文', exact: true }).click()
  await page.locator('dialog[open] #detail-chunk-9.is-highlighted').waitFor()
  assert.equal(await page.getByRole('dialog').count(), 1)
  await page.getByRole('tab', { name: '引用片段' }).focus()
  await page.keyboard.press('ArrowLeft')
  assert.equal(await page.getByRole('tab', { name: '原文', exact: true }).getAttribute('aria-selected'), 'true')
  await page.keyboard.press('End')
  assert.equal(await page.getByRole('tab', { name: '引用片段' }).getAttribute('aria-selected'), 'true')
  await page.screenshot({ path: fileURLToPath(new URL('test-drawer.png', artifacts)), fullPage: true })
  await page.getByRole('button', { name: '修改正文', exact: true }).click()
  await page.getByRole('textbox', { name: '编辑资料正文' }).fill('# 更新后的测试资料\n\n保存后应提示重新提问。')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await page.getByText('修改已保存，正在重新处理资料。', { exact: true }).waitFor()
  await page.getByRole('button', { name: '关闭资料详情', exact: true }).click()
  await page.getByText('引用资料已发生变化。这是更新前的回答，请重新提问以获取最新内容。', { exact: true }).waitFor()
  assert.equal(await page.evaluate(() => document.activeElement?.classList.contains('source-open')), true, 'closing the drawer must restore focus to its source button')
  await page.screenshot({ path: fileURLToPath(new URL('test-answer.png', artifacts)), fullPage: true })

  await page.getByRole('link', { name: '资料库', exact: true }).click()
  await page.getByRole('link', { name: '知识问答', exact: true }).click()
  assert.match(await page.locator('.answer-body').innerText(), /RDB/)
  await page.waitForTimeout(2100)
  await page.getByRole('textbox', { name: '向知识库提问' }).fill('只有部分资料时如何回答？')
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  await page.getByText('现有资料只能回答部分问题', { exact: true }).waitFor()
  await page.getByRole('textbox', { name: '向知识库提问' }).fill('缺少资料的问题')
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  await page.locator('.answer-refused').waitFor()
  assert.equal(await page.locator('.notice-error').count(), 0)

  await page.getByRole('link', { name: '资料库', exact: true }).click()
  await page.getByRole('button', { name: 'Redis 持久化机制', exact: true }).click()
  await page.getByRole('button', { name: '删除资料', exact: true }).click()
  assert.equal(deleted, false)
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await page.locator('dialog[open]').waitFor({ state: 'hidden' })
  assert.equal(deleted, true)
  await page.getByLabel('选择要上传的资料').setInputFiles({ name: '浏览器上传验证.md', mimeType: 'text/markdown', buffer: Buffer.from('# 浏览器上传验证\n\n一份只用于测试的资料。') })
  await page.getByRole('button', { name: '浏览器上传验证', exact: true }).waitFor()

  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: fileURLToPath(new URL('test-library-mobile.png', artifacts)), fullPage: true })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'library must not overflow horizontally')
  await page.getByRole('link', { name: '知识问答', exact: true }).click()
  await page.getByRole('button', { name: '新问题', exact: true }).click()
  await page.screenshot({ path: fileURLToPath(new URL('test-ask-mobile.png', artifacts)), fullPage: true })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'ask page must not overflow horizontally')
  assert.deepEqual(pageErrors, [])
  console.log('PASS: search, question, citation order, document highlight, update/stale answer, route persistence, partial answer, refusal, delete confirmation, upload, narrow viewports; no page errors.')
  console.log('API responses in this test are isolated browser fixtures; no existing documents were changed.')
} catch (error) {
  console.error({ pageErrors, consoleErrors, interceptedPaths })
  await page.screenshot({ path: fileURLToPath(new URL('test-failure.png', artifacts)), fullPage: true })
  throw error
} finally {
  await browser.close()
}
