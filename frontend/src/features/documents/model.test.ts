import { describe, expect, it } from 'vitest'

import { previewSource } from './model'

describe('previewSource', () => {
  it('drops a leading frontmatter block so its closing fence is not rendered as a heading', () => {
    expect(previewSource('---\ntitle: 笔记\ntags:\n  - a\n---\n# 正文\n[链接](https://example.com)'))
      .toBe('# 正文\n[链接](https://example.com)')
    expect(previewSource('---\r\ntags: x\r\n---\r\n正文')).toBe('正文')
  })

  it('keeps content without frontmatter and horizontal rules inside the body', () => {
    const content = '# 标题\n\n第一段\n\n---\n\n第二段'
    expect(previewSource(content)).toBe(content)
    expect(previewSource('正文\n---\ntags: x\n---\n')).toBe('正文\n---\ntags: x\n---\n')
  })
})
