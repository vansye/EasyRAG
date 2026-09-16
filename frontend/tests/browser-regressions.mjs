import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const frontendRoot = fileURLToPath(new URL('../', import.meta.url))
const base = 'http://127.0.0.1:15173'
const server = await createServer({
  root: frontendRoot,
  server: { host: '127.0.0.1', port: 15173, strictPort: true },
  clearScreen: false,
})
let browser
const results = []

async function fixture() {
  const context = await browser.newContext()
  const page = await context.newPage()
  page.setDefaultTimeout(8000)
  const docs = [1, 2].map(id => ({
    id, title: 'Regression document ' + id, content: '# Original ' + id,
    tags: [], source_type: 'UPLOAD', source_uri: 'regression.md', index_status: 'INDEXED',
    index_error: null, chunk_count: 1, created_at: '2026-09-15T12:00:00', updated_at: '2026-09-15T12:00:00',
  }))
  const runtime = {
    state: 'READY', rag_available: true,
    llm: { configured: true, provider: 'openai', model: 'fixture-model' },
    embedding: { provider: 'ollama', model: 'fixture-embedding', dim: 1024 },
  }
  const answer = {
    answer: 'Fixture answer [1]', status: 'ANSWERED',
    sources: [{ chunk_id: 10, document_id: 1, title: docs[0].title, text: docs[0].content, byte_start: 0, byte_end: 12, heading_path: 'Original' }],
    trace: [{ round_index: 1, query: 'Question', decision: 'SUFFICIENT', retrieved: [{ chunk_id: 10, document_id: 1, rank: 1, score: 0.8 }] }],
  }
  const logs = { writes: [], confirmDialogs: [], pageErrors: [], externalRequests: [] }
  const controls = {
    acceptDiscard: false, holdWrite: false, mutation: null, onQuestion: null,
    holdHealth: false, healthWaiters: [], afterWrite: null, readyCalls: 0, onUpload: null, failHealth: false,
  }
  let mutationStarted
  controls.mutationStarted = new Promise(resolve => { mutationStarted = resolve })
  page.on('dialog', async dialog => {
    logs.confirmDialogs.push(dialog.message())
    if (controls.acceptDiscard) await dialog.accept()
    else await dialog.dismiss()
  })
  page.on('pageerror', error => logs.pageErrors.push(error.message))
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.origin !== base) { logs.externalRequests.push(url.href); return route.abort() }
    if (url.pathname === '/health') {
      if (controls.holdHealth) await new Promise(resolve => { controls.healthWaiters.push(resolve) })
      if (controls.failHealth) { controls.failHealth = false; return route.abort('failed') }
      return route.fulfill({ json: { status: 'UP', db: { status: 'UP' } } })
    }
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (url.pathname === '/api/runtime') return route.fulfill({ json: runtime })
    if (url.pathname === '/api/admin/ready') {
      controls.readyCalls++
      runtime.state = 'READY'
      return route.fulfill({ json: { state: 'READY', recovered: 0 } })
    }
    if (url.pathname === '/api/questions') {
      if (controls.onQuestion) return controls.onQuestion(route)
      return route.fulfill({ json: answer })
    }
    if (url.pathname === '/api/documents') {
      if (request.method() === 'POST') {
        const item = { ...docs[0], id: 3, title: 'Uploaded note', content: '# Uploaded', index_status: 'PENDING' }
        docs.push(item)
        controls.onUpload?.()
        return route.fulfill({ status: 201, json: { id: item.id, title: item.title, source_type: 'UPLOAD', index_status: 'PENDING' } })
      }
      return route.fulfill({ json: { items: docs, total: docs.length } })
    }
    const match = url.pathname.match(/^\/api\/documents\/(\d+)(\/chunks|\/reindex)?$/)
    if (match) {
      const item = docs.find(doc => doc.id === Number(match[1]))
      if (request.method() === 'PUT') {
        const body = request.postDataJSON()
        logs.writes.push({ id: item.id, body })
        if (controls.holdWrite) await new Promise(resolve => { controls.mutation = resolve; mutationStarted() })
        item.content = body.content
        controls.afterWrite?.()
        return route.fulfill({ json: { id: item.id, index_status: 'PENDING', reindexed: true } })
      }
      if (request.method() === 'DELETE') {
        docs.splice(docs.indexOf(item), 1)
        return route.fulfill({ status: 204 })
      }
      if (match[2] === '/reindex') return route.fulfill({ status: 202, json: { id: item.id, index_status: 'PENDING' } })
      if (match[2] === '/chunks') return route.fulfill({ json: { items: [{ id: item.id * 10, seq: 0, text: item.content, byte_start: 0, byte_end: item.content.length, heading_path: 'Original', token_count: 10 }] } })
      return route.fulfill({ json: item })
    }
    return route.fulfill({ status: 404, json: { error: 'Unspecified fixture API' } })
  })
  return { context, page, docs, runtime, logs, controls }
}

// Wait for the router to settle even when it rejects browser Back.
async function back(page) {
  await page.evaluate(async () => {
    const { default: router } = await import('/src/app/router.ts')
    await new Promise(resolve => {
      const remove = router.afterEach(() => { remove(); resolve() })
      window.history.back()
    })
  })
}

async function run(name, action) {
  const test = await fixture()
  try {
    await action(test)
    assert.deepEqual(test.logs.pageErrors, [])
    assert.deepEqual(test.logs.externalRequests, [])
    results.push({ name, passed: true })
    console.log('PASS', name)
  } catch (error) {
    results.push({ name, passed: false, error: error.message })
    console.error('FAIL', name, error.message)
  } finally {
    test.controls.holdHealth = false
    for (const release of test.controls.healthWaiters) release()
    test.controls.mutation?.()
    await test.context.close()
  }
}

try {
  await server.listen()
  browser = await chromium.launch({ channel: 'chrome', headless: true })

  await run('browser Back keeps a dirty draft until discard is accepted', async ({ page, docs, logs, controls }) => {
    await page.goto(base)
    await page.waitForLoadState('networkidle')
    await page.locator('.document-title').first().click()
    await page.locator('.drawer-edit').click()
    const draft = '# Unsaved work'
    await page.locator('#document-editor').fill(draft)
    const documentUrl = page.url()
    await back(page)
    assert.equal(logs.confirmDialogs.length, 1)
    await page.waitForURL(documentUrl)
    assert.equal(await page.locator('#document-editor').inputValue(), draft)
    controls.acceptDiscard = true
    await back(page)
    await page.waitForURL(base + '/')
    await page.locator('dialog[open]').waitFor({ state: 'hidden' })
    assert.equal(logs.confirmDialogs.length, 2)
    await page.locator('.document-title').first().click()
    await page.locator('.drawer-edit').click()
    assert.equal(await page.locator('#document-editor').inputValue(), docs[0].content)
    assert.equal(logs.writes.length, 0)
  })

  await run('explicit close asks once and preserves a rejected draft', async ({ page, logs, controls }) => {
    await page.goto(base + '/?document=1')
    await page.locator('.drawer-edit').click()
    await page.locator('#document-editor').fill('# Unsaved close')
    await page.locator('.drawer-header .icon-button').click()
    assert.equal(await page.locator('#document-editor').inputValue(), '# Unsaved close')
    assert.equal(logs.confirmDialogs.length, 1)
    controls.acceptDiscard = true
    await page.locator('.drawer-header .icon-button').click()
    await page.locator('dialog[open]').waitFor({ state: 'hidden' })
    assert.equal(logs.confirmDialogs.length, 2)
  })

  await run('saving holds document identity and invalidates its answer', async ({ page, docs, logs, controls }) => {
    await page.goto(base + '/ask')
    await page.waitForLoadState('networkidle')
    await page.locator('#knowledge-question').fill('Question about document 1')
    await page.locator('.send-button').click()
    await page.locator('.answer-paper').waitFor()
    await page.locator('#citation-1 .source-open').click()
    await page.locator('.drawer-edit').click()
    await page.locator('#document-editor').fill('# Saved update for document 1')
    const documentUrl = page.url()
    controls.holdWrite = true
    await page.locator('.drawer-footer .button-primary').click()
    await controls.mutationStarted
    await back(page)
    await page.waitForURL(documentUrl)
    assert.equal(logs.confirmDialogs.length, 0)
    assert.equal(await page.locator('#document-editor').inputValue(), '# Saved update for document 1')
    assert.equal(await page.locator('.drawer-header .icon-button').isDisabled(), true)
    controls.mutation()
    await page.waitForFunction(() => !document.querySelector('.drawer-header .icon-button').disabled)
    await page.locator('.drawer-notice.notice-success').waitFor()
    assert.equal(docs[0].content, '# Saved update for document 1')
    assert.equal(logs.writes[0].id, 1)
    await page.locator('.drawer-header .icon-button').click()
    await page.locator('.answer-notice').waitFor()
    await page.locator('.app-nav a[href="/"]').click()
    await page.locator('.document-title').nth(1).click()
    await page.locator('#document-heading').filter({ hasText: docs[1].title }).waitFor()
    assert.equal(await page.locator('.drawer-notice.notice-success').count(), 0)
    assert.equal(await page.locator('.original-content').innerText(), '# Original 2')
  })

  await run('successful deletion can close through the navigation guard', async ({ page, docs }) => {
    await page.goto(base + '/?document=1')
    await page.locator('.delete-button').click()
    await page.locator('.button-danger').click()
    await page.locator('dialog[open]').waitFor({ state: 'hidden' })
    assert.deepEqual(docs.map(doc => doc.id), [2])
    assert.equal(new URL(page.url()).searchParams.has('document'), false)
  })

  await run('a stale READY refresh cannot overwrite a newer mutation conflict', async ({ page, runtime, controls }) => {
    await page.goto(base + '/ask')
    await page.waitForLoadState('networkidle')
    await page.locator('#knowledge-question').fill('Question during mutation')
    controls.holdHealth = true
    const oldRuntime = page.waitForResponse(response => response.url() === base + '/api/runtime')
    await page.locator('.connection-state').click()
    assert.equal((await (await oldRuntime).json()).state, 'READY')
    controls.onQuestion = route => {
      runtime.state = 'MUTATING'
      return route.fulfill({ status: 503, json: { error: 'Fixture mutation in progress', state: 'MUTATING' } })
    }
    await page.locator('.send-button').click()
    await page.locator('.question-error').waitFor()
    assert.equal(controls.healthWaiters.length, 1)
    assert.equal(await page.locator('.send-button').isDisabled(), true)
    const freshRuntime = page.waitForResponse(response => response.url() === base + '/api/runtime')
    controls.healthWaiters[0]()
    assert.equal((await (await freshRuntime).json()).state, 'MUTATING')
    assert.equal(await page.locator('.send-button').isDisabled(), true)
    assert.equal(controls.healthWaiters.length, 2)
    controls.holdHealth = false
    controls.healthWaiters[1]()
    await page.waitForFunction(() => !document.querySelector('.connection-state').disabled)
    assert.equal(await page.locator('.send-button').isDisabled(), true)
    assert.equal(await page.locator('.app-notices .notice-warm').count(), 1)
  })

  await run('successful recovery refreshes after a pending old snapshot', async ({ page, runtime, controls }) => {
    runtime.state = 'RECOVERY_REQUIRED'
    await page.goto(base + '/ask')
    await page.waitForLoadState('networkidle')
    await page.locator('#knowledge-question').fill('Question after recovery')
    controls.holdHealth = true
    const oldRuntime = page.waitForResponse(response => response.url() === base + '/api/runtime')
    await page.locator('.connection-state').click()
    assert.equal((await (await oldRuntime).json()).state, 'RECOVERY_REQUIRED')
    const readyResponse = page.waitForResponse(response => response.url() === base + '/api/admin/ready')
    await page.getByRole('button', { name: '确认就绪', exact: true }).click()
    await readyResponse
    assert.equal(controls.readyCalls, 1)
    assert.equal(controls.healthWaiters.length, 1)
    controls.holdHealth = false
    controls.healthWaiters[0]()
    await page.waitForFunction(() => !document.querySelector('.connection-state').disabled)
    await page.waitForFunction(() => !document.querySelector('.send-button').disabled)
    assert.equal(await page.locator('.app-notices .notice-warm').count(), 0)
  })

  await run('successful save requests a fresh state after an old poll', async ({ page, runtime, controls }) => {
    await page.goto(base + '/?document=1')
    await page.waitForLoadState('networkidle')
    // The modal makes the header inert; invoke the same refresh action directly.
    controls.holdHealth = true
    const oldRuntime = page.waitForResponse(response => response.url() === base + '/api/runtime')
    await page.evaluate(async () => {
      const { useGateStore } = await import('/src/shared/gate.ts')
      void useGateStore().refresh()
    })
    assert.equal((await (await oldRuntime).json()).state, 'READY')
    await page.locator('.drawer-edit').click()
    await page.locator('#document-editor').fill('# Updated while polling')
    controls.afterWrite = () => { runtime.state = 'MUTATING' }
    const saved = page.waitForResponse(response => response.request().method() === 'PUT')
    await page.locator('.drawer-footer .button-primary').click()
    await saved
    await page.locator('.drawer-notice.notice-success').waitFor()
    assert.equal(controls.healthWaiters.length, 1)
    controls.holdHealth = false
    controls.healthWaiters[0]()
    await page.waitForFunction(() => !document.querySelector('.drawer-header .icon-button').disabled)
    assert.equal(await page.locator('.app-notices .notice-warm').count(), 1)
    assert.equal(await page.locator('.drawer-edit').isDisabled(), true)
  })


  await run('upload completion invalidates an older runtime snapshot', async ({ page, runtime, controls }) => {
    await page.goto(base)
    await page.waitForLoadState('networkidle')
    controls.holdHealth = true
    const oldRuntime = page.waitForResponse(response => response.url() === base + '/api/runtime')
    await page.locator('.connection-state').click()
    assert.equal((await (await oldRuntime).json()).state, 'READY')
    controls.onUpload = () => { runtime.state = 'MUTATING' }
    await page.evaluate(async () => {
      const { useDocumentStore } = await import('/src/features/documents/store.ts')
      await useDocumentStore().upload(new File(['# Uploaded'], 'upload.md', { type: 'text/markdown' }))
    })
    assert.equal(controls.healthWaiters.length, 1)
    controls.holdHealth = false
    controls.healthWaiters[0]()
    await page.waitForFunction(() => !document.querySelector('.connection-state').disabled)
    assert.equal(await page.locator('.app-notices .notice-warm').count(), 1)
  })

  await run('a stale refresh failure cannot hide a newer operation conflict', async ({ page, runtime, controls }) => {
    await page.goto(base + '/ask')
    await page.waitForLoadState('networkidle')
    await page.locator('#knowledge-question').fill('Question during mutation')
    controls.holdHealth = true
    const oldRuntime = page.waitForResponse(response => response.url() === base + '/api/runtime')
    await page.locator('.connection-state').click()
    await oldRuntime
    controls.onQuestion = route => {
      runtime.state = 'MUTATING'
      return route.fulfill({ status: 503, json: { error: 'Fixture mutation in progress', state: 'MUTATING' } })
    }
    await page.locator('.send-button').click()
    await page.locator('.question-error').waitFor()
    const freshRuntime = page.waitForResponse(response => response.url() === base + '/api/runtime')
    controls.failHealth = true
    controls.healthWaiters[0]()
    await freshRuntime
    assert.equal(await page.locator('.app-notices .notice-error').count(), 0)
    assert.equal(await page.locator('.send-button').isDisabled(), true)
    controls.holdHealth = false
    controls.healthWaiters[1]()
    await page.waitForFunction(() => !document.querySelector('.connection-state').disabled)
    assert.equal(await page.locator('.app-notices .notice-warm').count(), 1)
  })


  console.log('REGRESSIONS_COMPLETE', JSON.stringify(results))
  if (results.some(result => !result.passed)) process.exitCode = 1
} finally {
  await browser?.close()
  await server.close()
}
