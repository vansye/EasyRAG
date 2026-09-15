import type { Token, Tokens } from 'marked'
import { defineComponent, h, type PropType, type VNodeChild } from 'vue'

import { parseCitationText, type Citation } from '../model'

export default defineComponent({
  props: {
    tokens: { type: Array as PropType<Token[]>, required: true },
    citations: { type: Array as PropType<Citation[]>, required: true },
  },
  emits: { citation: (number: number) => Number.isInteger(number) },
  setup(props, { emit }) {
    function renderTokens(tokens: Token[], references = true): VNodeChild[] {
      return tokens.map((token): VNodeChild => {
        const children = () => renderTokens(token.tokens ?? [], references)
        switch (token.type) {
          case 'space': case 'def': return null
          case 'paragraph': return h('p', children())
          case 'heading': return h(`h${Math.min(token.depth + 2, 6)}`, children())
          case 'strong': return h('strong', children())
          case 'em': return h('em', children())
          case 'del': return h('del', children())
          case 'br': return h('br')
          case 'hr': return h('hr')
          case 'blockquote': return h('blockquote', children())
          case 'code': return h('pre', [h('code', token.text)])
          case 'codespan': return h('code', token.text)
          case 'html': case 'escape': return token.text
          case 'image': return token.text
          case 'checkbox': return h('span', { 'aria-hidden': 'true' }, token.checked ? '☑ ' : '☐ ')
          case 'link': {
            const label = renderTokens(token.tokens ?? [], false)
            return /^https?:\/\//i.test(token.href)
              ? h('a', { href: token.href, target: '_blank', rel: 'noopener noreferrer' }, label) : label
          }
          case 'list': return h(token.ordered ? 'ol' : 'ul', { start: token.ordered && token.start !== 1 ? token.start : undefined },
            (token.items as Tokens.ListItem[]).map((item) => h('li', renderTokens(item.tokens, references))))
          case 'table': {
            const table = token as Tokens.Table
            return h('div', { class: 'answer-table-wrap', tabindex: 0, 'aria-label': '回答中的表格' }, [h('table', [
              h('thead', [h('tr', table.header.map((cell) => h('th', { scope: 'col' }, renderTokens(cell.tokens, references))))]),
              h('tbody', table.rows.map((row) => h('tr', row.map((cell) => h('td', renderTokens(cell.tokens, references)))))),
            ])])
          }
          case 'text': {
            if (token.tokens) return children()
            if (!references) return token.text
            const byNumber = new Map(props.citations.map((citation) => [citation.number, citation]))
            return parseCitationText(token.text, byNumber).map((segment) => segment.type === 'text' ? segment.text : h('button', {
              type: 'button', class: 'inline-citation',
              'aria-label': `查看引用 ${segment.citation.number}：${segment.citation.source.title}`,
              onClick: () => emit('citation', segment.citation.number),
            }, String(segment.citation.number)))
          }
          default: return token.raw
        }
      })
    }
    // Every node is created through Vue; model HTML is always rendered as literal text.
    return () => h('div', { class: 'answer-body' }, renderTokens(props.tokens))
  },
})
