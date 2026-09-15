import { Lexer } from 'marked'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { describe, expect, it } from 'vitest'

import type { AnsweredQuestion } from '@/shared/api/types'
import { presentAnswer } from '../model'
import AnswerBody from './AnswerBody'

function result(answer: string): AnsweredQuestion {
  return {
    answer, status: 'ANSWERED',
    sources: [2, 9].map((id) => ({ chunk_id: id, document_id: id, title: `资料 ${id}`, text: '原文', byte_start: 0, byte_end: 6, heading_path: '' })),
    trace: [{ round_index: 1, query: '问题', decision: 'SUFFICIENT', retrieved: [
      { chunk_id: 9, document_id: 9, rank: 1, score: 0.1 },
      { chunk_id: 2, document_id: 2, rank: 2, score: 0.2 },
    ] }],
  }
}

async function render(answer: string) {
  const html = await renderToString(createSSRApp(AnswerBody, { tokens: Lexer.lex(answer), citations: presentAnswer(result(answer)).citations }))
  return html.replace(/<!--[\s\S]*?-->/g, '')
}

describe('formatted answers', () => {
  it('formats lists and emphasis while retaining ranked citation buttons', async () => {
    const html = await render('* **活动时间**：每周三 [1]\n* 活动地点：阅览室 [2]')
    expect(html).toContain('<ul>')
    expect(html).toContain('<strong>活动时间</strong>')
    expect(html).toContain('aria-label="查看引用 1：资料 9"')
    expect(html).toContain('aria-label="查看引用 2：资料 2"')
  })

  it('preserves code literally and excludes code indexes from cited sources', async () => {
    const answer = '使用 `values[1]`。\n\n```html\n<img src=x onerror=alert(1)> [2]\n```'
    expect(presentAnswer(result(answer)).citations).toEqual([])
    const html = await render(answer)
    expect(html).toContain('<code>values[1]</code>')
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt; [2]')
    expect(html).not.toContain('<button')
  })

  it('escapes raw HTML and never loads model-supplied images', async () => {
    const html = await render('<script>alert(1)</script>\n\n![说明](https://example.com/track.png)')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;script&gt;')
    expect(html).toContain('说明')
  })

  it('allows web links but does not create executable links', async () => {
    const html = await render('[文档](https://example.com/docs) [危险链接](javascript:alert%281%29)')
    expect(html).toContain('href="https://example.com/docs"')
    expect(html).not.toContain('href="javascript:')
    expect(html).toContain('危险链接')
  })
})
