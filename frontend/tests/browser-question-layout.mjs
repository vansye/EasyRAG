import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const baseUrl = process.env.EASYRAG_PREVIEW_URL || 'http://127.0.0.1:5173'
const artifacts = new URL('../.verification/question-layout/', import.meta.url)
await mkdir(artifacts, { recursive: true })
const browser = await chromium.launch({ headless: true, ...(process.env.EASYRAG_BROWSER_CHANNEL ? { channel: process.env.EASYRAG_BROWSER_CHANNEL } : {}) })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
page.setDefaultTimeout(12000)
const pageErrors = []
page.on('pageerror', error => pageErrors.push(error.message))
const longPath = '算法笔记 > 字符串处理 > 最长不重复子串的完整推导与边界条件 > '.repeat(6)
const sources = [1, 2].map(number => ({
  chunk_id: number, document_id: number,
  title: `资料 ${number}：${'很长的标题与函数名称'.repeat(15)}`,
  heading_path: longPath,
  text: `以下是原文中需要保持可读的代码与链接。\n${'long_identifier_'.repeat(50)}\nhttps://example.org/${'source/'.repeat(60)}`,
  byte_start: 0, byte_end: 10,
}))
const answer = {
  answer: '根据第一份资料说明边界 [1]，再核对第二份资料 [2]。\n\n```python\n' + 'long_identifier_'.repeat(60) + '\n```\n\n| 字段 | 值 |\n| --- | --- |\n| 条件 | ' + 'long_value_'.repeat(50) + ' |',
  status: 'ANSWERED', sources,
  trace: [{ round_index: 1, query: '核对两份资料', decision: 'SUFFICIENT', retrieved: sources.map((source, index) => ({ chunk_id: source.chunk_id, document_id: source.document_id, rank: index + 1, score: 0.8 - index / 10 })) }],
  model: { provider: 'openai', model: 'layout-test-model' }, elapsed_ms: 1200, created_at: '2026-09-16T10:00:00',
}
const history = [{ ...answer, id: 1, question: '已有的历史问题' }]
let modelRequests = 0
const measurements = []

await page.route('**/health', route => route.fulfill({ json: { status: 'UP', service: 'easyrag-server', db: { status: 'UP' } } }))
await page.route(/\/api\//, route => {
  const request = route.request()
  const url = new URL(request.url())
  if (!url.pathname.startsWith('/api/')) return route.continue()
  if (url.pathname === '/api/runtime') return route.fulfill({ json: { state: 'READY', rag_available: true, llm: { configured: true, provider: 'openai', model: 'layout-test-model' }, embedding: { provider: 'ollama', model: 'bge-m3', dim: 1024 } } })
  if (url.pathname === '/api/documents') return route.fulfill({ json: { total: 0, items: [] } })
  if (url.pathname === '/api/question-history') return route.fulfill({ json: { total: history.length, items: history.map(({ id, question, status, model, elapsed_ms, created_at }) => ({ id, question, status, model, elapsed_ms, created_at })) } })
  if (url.pathname.startsWith('/api/question-history/')) return route.fulfill({ json: history.find(item => item.id === Number(url.pathname.split('/').at(-1))) })
  if (url.pathname === '/api/questions') {
    modelRequests++
    const record = { ...answer, id: history.length + 1, question: request.postDataJSON().question }
    history.unshift(record)
    return route.fulfill({ json: { ...answer, history_id: record.id } })
  }
  throw new Error(`Unexpected API request: ${request.method()} ${url.pathname}`)
})

async function verifyLayout(state, width) {
  await page.setViewportSize({ width, height: 1000 })
  const boxes = await page.evaluate(() => {
    const rect = selector => {
      const box = document.querySelector(selector).getBoundingClientRect()
      return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width }
    }
    return {
      main: rect('.ask-page'), history: rect('.question-history'), citations: rect('.citation-grid'),
      cards: [...document.querySelectorAll('.source-card')].map(card => { const box = card.getBoundingClientRect(); return { left: box.left, right: box.right, width: box.width } }),
      scrollWidth: document.documentElement.scrollWidth, viewportWidth: document.documentElement.clientWidth,
    }
  })
  measurements.push({ state, width, ...boxes })
  await writeFile(new URL('measurements.json', artifacts), JSON.stringify(measurements, null, 2))
  assert.equal(boxes.cards.length, 2)
  for (const card of boxes.cards) {
    assert.ok(card.left >= boxes.main.left - 1 && card.right <= boxes.main.right + 1,
      `${state} at ${width}px: source card [${card.left}, ${card.right}] escapes answer column [${boxes.main.left}, ${boxes.main.right}]`)
  }
  assert.ok(boxes.scrollWidth <= boxes.viewportWidth + 1, `${state} at ${width}px: page overflows horizontally`)
  if (width > 1000) {
    assert.ok(boxes.main.right < boxes.history.left, `${state}: answer and history columns overlap`)
  } else {
    assert.ok(boxes.history.top >= boxes.main.bottom, `${state}: history overlaps stacked answer`)
  }
  if (width === 1440 || width === 390) await page.screenshot({ path: fileURLToPath(new URL(`${state}-${width}.png`, artifacts)), fullPage: true })
}

try {
  await page.goto(`${baseUrl}/ask`)
  await page.locator('[data-history-id="1"] .history-select').click()
  await page.locator('.citation-snapshots').waitFor()
  for (const width of [1440, 1024, 1000, 768, 390]) await verifyLayout('history', width)
  assert.equal(modelRequests, 0, 'Opening history must not call a model')

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: '重新提问', exact: true }).click()
  await page.locator('.citation-grid:not(.citation-snapshots)').waitFor()
  for (const width of [1440, 1100, 1024, 1000, 768, 390]) await verifyLayout('current', width)
  assert.equal(modelRequests, 1)

  await page.locator('[data-history-id="1"] .history-select').click()
  await page.locator('.citation-snapshots').waitFor()
  await verifyLayout('history-after-answer', 1440)
  await page.reload()
  await page.locator('.history-select').first().click()
  await page.locator('.citation-snapshots').waitFor()
  await verifyLayout('history-after-reload', 390)
  assert.equal(modelRequests, 1, 'History transitions must not rerun a question')
  assert.deepEqual(pageErrors, [])
  console.log(`Question layout regression passed: ${measurements.length} state/viewport checks; no overlap or page overflow.`)
} catch (error) {
  await page.screenshot({ path: fileURLToPath(new URL('failure.png', artifacts)), fullPage: true })
  throw error
} finally {
  await browser.close()
}
