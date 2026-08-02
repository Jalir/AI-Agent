/** Markdown → 消毒 HTML：markdown-it + 表格 + highlight.js + DOMPurify */

import MarkdownIt from 'markdown-it'
import multimdTable from 'markdown-it-multimd-table'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('svg', xml)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

md.use(multimdTable, {
  multiline: true,
  rowspan: true,
  headerless: true,
  multibody: true,
})

md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const info = token.info ? md.utils.unescapeAll(token.info).trim() : ''
  const lang = info.split(/\s+/g)[0] || ''
  let body: string
  if (lang && hljs.getLanguage(lang)) {
    try {
      body = hljs.highlight(token.content, { language: lang, ignoreIllegals: true }).value
    } catch {
      body = md.utils.escapeHtml(token.content)
    }
  } else {
    body = md.utils.escapeHtml(token.content)
  }
  const langAttr = lang ? ` language-${md.utils.escapeHtml(lang)}` : ''
  const langLabel = lang
    ? `<span class="md-code-lang">${md.utils.escapeHtml(lang)}</span>`
    : `<span class="md-code-lang md-code-lang-empty"></span>`
  const copyBtn =
    `<button type="button" class="md-code-copy" aria-label="复制代码" title="复制代码">` +
    `<span class="md-code-copy-icon" aria-hidden="true"></span>` +
    `<span class="md-code-copy-label">复制</span>` +
    `</button>`
  return (
    `<div class="md-code-block">` +
    `<div class="md-code-header">${langLabel}${copyBtn}</div>` +
    `<pre class="hljs"><code class="hljs${langAttr}">${body}</code></pre>` +
    `</div>\n`
  )
}

md.renderer.rules.link_open = (tokens, idx, options, _env, self) => {
  const token = tokens[idx]
  const hrefIdx = token.attrIndex('href')
  if (hrefIdx >= 0) {
    const href = token.attrs?.[hrefIdx]?.[1] || ''
    if (/^https?:\/\//i.test(href)) {
      token.attrSet('target', '_blank')
      token.attrSet('rel', 'noopener noreferrer')
    }
  }
  return self.renderToken(tokens, idx, options)
}

const defaultTableOpen =
  md.renderer.rules.table_open ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
const defaultTableClose =
  md.renderer.rules.table_close ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.table_open = (tokens, idx, options, env, self) =>
  `<div class="md-table-wrap">${defaultTableOpen(tokens, idx, options, env, self)}`
md.renderer.rules.table_close = (tokens, idx, options, env, self) =>
  `${defaultTableClose(tokens, idx, options, env, self)}</div>\n`

const PURIFY = {
  USE_PROFILES: { html: true },
  ADD_ATTR: ['target', 'rel', 'class', 'type', 'title', 'aria-label', 'aria-hidden'],
  ALLOWED_TAGS: [
    'a',
    'abbr',
    'b',
    'blockquote',
    'br',
    'button',
    'code',
    'del',
    'div',
    'em',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'hr',
    'i',
    'img',
    'ins',
    'kbd',
    'li',
    'mark',
    'ol',
    'p',
    'pre',
    's',
    'span',
    'strong',
    'sub',
    'sup',
    'table',
    'tbody',
    'td',
    'tfoot',
    'th',
    'thead',
    'tr',
    'ul',
  ],
  ALLOWED_ATTR: [
    'href',
    'title',
    'target',
    'rel',
    'class',
    'colspan',
    'rowspan',
    'align',
    'alt',
    'src',
    'type',
    'aria-label',
    'aria-hidden',
  ],
} as const

export function renderMarkdown(source: string): string {
  const raw = (source || '').trimEnd()
  if (!raw) return ''
  const html = md.render(raw)
  return DOMPurify.sanitize(html, PURIFY as DOMPurify.Config)
}
