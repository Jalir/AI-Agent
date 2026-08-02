<template>
  <div :class="['message-bubble', message.role]">
    <div class="avatar">
      <template v-if="message.role === 'user'">U</template>
      <template v-else-if="message.role === 'assistant'">AI</template>
      <template v-else>!</template>
    </div>
    <div class="bubble-col">
      <div
        :class="[
          'bubble-content',
          message.role,
            {
              streaming: isStreaming && hasStreamableBody,
              thinking:
                message.role === 'assistant' &&
                !message.content &&
                !fileAttachments.length &&
                !imageAttachments.length &&
                !audioAttachments.length &&
                !xhsCards.length &&
                !chartBlocks.length &&
                !hasApprovalCard &&
                isStreaming,
            },
        ]"
      >
        <!-- 思考态：仅流式中转圈，避免空气泡永久 spinner -->
        <div
          v-if="message.role === 'assistant' && !message.content && !fileAttachments.length && !imageAttachments.length && !audioAttachments.length && !xhsCards.length && !chartBlocks.length && !hasApprovalCard && isStreaming"
          class="thinking-row"
        >
          <span class="thinking-spinner" aria-hidden="true"></span>
          <div class="status-wipe" aria-live="polite">
            <span
              v-if="outgoingStatus"
              class="status-line status-out"
            >{{ outgoingStatus }}</span>
            <span
              :key="statusKey"
              class="status-line status-in"
            >{{ displayStatus }}</span>
          </div>
        </div>
        <div
          v-else-if="message.role === 'assistant' && (message.content || fileAttachments.length || imageAttachments.length || audioAttachments.length || xhsCards.length || chartBlocks.length || hasApprovalCard)"
          class="assistant-body"
        >
          <div
            v-if="displayContent"
            class="md-body"
            v-html="renderedHtml"
            @click="onMdClick"
          ></div>
          <div
            v-if="hasApprovalCard"
            class="approval-card"
          >
            <p class="approval-question">{{ message.approval?.question }}</p>
            <div
              v-if="showApprovalDraft && message.approval?.status === 'pending'"
              class="approval-draft"
            >
              <label class="approval-field">
                <span>收件人</span>
                <input
                  v-model="draftTo"
                  type="email"
                  class="approval-input"
                  :disabled="approvalBusy || !message.approval?.editable"
                  autocomplete="off"
                />
              </label>
              <label class="approval-field">
                <span>主题</span>
                <input
                  v-model="draftSubject"
                  type="text"
                  class="approval-input"
                  :disabled="approvalBusy || !message.approval?.editable"
                  autocomplete="off"
                />
              </label>
              <label class="approval-field">
                <span>正文</span>
                <textarea
                  v-model="draftBody"
                  class="approval-textarea"
                  rows="5"
                  :disabled="approvalBusy || !message.approval?.editable"
                ></textarea>
              </label>
            </div>
            <div
              v-if="message.approval?.status === 'pending'"
              class="approval-actions"
            >
              <button
                type="button"
                class="approval-btn cancel"
                :disabled="approvalBusy"
                @click="emit('decide', false)"
              >取消</button>
              <button
                type="button"
                class="approval-btn confirm"
                :disabled="approvalBusy"
                @click="onConfirmApproval"
              >确认</button>
            </div>
            <p
              v-else-if="message.approval?.status === 'cancelled'"
              class="approval-ended"
            >已取消</p>
          </div>
          <div
            v-if="chartBlocks.length"
            class="chart-block-list"
          >
            <ChartBlock
              v-for="(c, ci) in chartBlocks"
              :key="'chart-' + (c.chart_id || '') + '-' + ci"
              :option="c.option"
              :title="c.title"
            />
          </div>
          <div
            v-if="xhsCards.length"
            class="xhs-card-list"
          >
            <article
              v-for="card in xhsCards"
              :key="'xhs-' + card.index"
              class="xhs-card"
            >
              <header class="xhs-card-head">
                <span class="xhs-card-index">{{ card.index }}</span>
                <h3 class="xhs-card-title">{{ card.title || `笔记 ${card.index}` }}</h3>
              </header>
              <a
                v-if="card.image_url"
                class="xhs-card-image-link"
                :href="card.image_url"
                target="_blank"
                rel="noopener noreferrer"
              >
                <img
                  class="xhs-card-image"
                  :src="card.image_url"
                  :alt="card.title || '配图'"
                  loading="lazy"
                />
              </a>
              <p
                v-if="card.body"
                class="xhs-card-body"
              >{{ card.body }}</p>
              <div
                v-if="card.tags?.length"
                class="xhs-card-tags"
              >
                <span
                  v-for="(tag, ti) in card.tags"
                  :key="ti"
                  class="xhs-card-tag"
                >#{{ tag }}</span>
              </div>
              <p
                v-if="card.error"
                class="xhs-card-error"
              >生成失败，请稍后重试</p>
            </article>
          </div>
          <div
            v-if="imageAttachments.length"
            class="bubble-attachments"
          >
            <a
              v-for="(att, ai) in imageAttachments"
              :key="'img-' + ai"
              class="bubble-image-link"
              :href="att.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <img
                class="bubble-image"
                :src="att.url"
                :alt="att.name || '图片'"
                loading="lazy"
              />
            </a>
          </div>
          <div
            v-if="audioAttachments.length"
            class="bubble-audio-list"
          >
            <div
              v-for="(att, ai) in audioAttachments"
              :key="'aud-' + ai"
              class="bubble-audio-item"
            >
              <span class="bubble-audio-label">{{ att.name || '录音.mp3' }}</span>
              <audio
                class="bubble-audio"
                :src="att.url"
                controls
                preload="metadata"
              />
            </div>
          </div>
          <div
            v-if="fileAttachments.length"
            class="bubble-file-list"
          >
            <a
              v-for="(att, fi) in fileAttachments"
              :key="fi"
              class="bubble-file-card"
              :href="att.url"
              target="_blank"
              rel="noopener noreferrer"
              :download="att.name || undefined"
            >
              <span class="bubble-file-icon" aria-hidden="true">{{ fileIcon(att) }}</span>
              <span class="bubble-file-meta">
                <span class="bubble-file-name">{{ att.name || '下载文件' }}</span>
                <span class="bubble-file-hint">点击下载保存</span>
              </span>
            </a>
          </div>
        </div>
        <div v-else class="user-body">
          <div
            v-if="imageAttachments.length"
            class="bubble-attachments"
          >
            <a
              v-for="(att, ai) in imageAttachments"
              :key="ai"
              class="bubble-image-link"
              :href="att.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <img
                class="bubble-image"
                :src="att.url"
                :alt="att.name || '图片'"
                loading="lazy"
              />
            </a>
          </div>
          <div
            v-if="audioAttachments.length"
            class="bubble-audio-list"
          >
            <div
              v-for="(att, ai) in audioAttachments"
              :key="'uaud-' + ai"
              class="bubble-audio-item"
            >
              <span class="bubble-audio-label">{{ att.name || '录音.mp3' }}</span>
              <audio
                class="bubble-audio"
                :src="att.url"
                controls
                preload="metadata"
              />
            </div>
          </div>
          <span v-if="message.content" class="bubble-text">{{ message.content }}</span>
        </div>
        <!-- 有正文时，光标已插入 markdown 末字后；仅附件/卡片流式时用外置光标 -->
        <span
          v-if="isStreaming && hasStreamableBody && !displayContent"
          class="streaming-cursor"
          aria-hidden="true"
        ></span>
      </div>

      <div
        v-if="showActions"
        class="usage-bar"
      >
        <button
          type="button"
          class="msg-copy-btn"
          :title="copied ? '已复制' : '复制消息'"
          :aria-label="copied ? '已复制' : '复制消息'"
          @click="copyMessage"
        >
          <svg v-if="!copied" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect
              x="9" y="9" width="11" height="11" rx="2"
              stroke="currentColor" stroke-width="1.8"
            />
            <path
              d="M5 15V5a2 2 0 0 1 2-2h10"
              stroke="currentColor" stroke-width="1.8" stroke-linecap="round"
            />
          </svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M5 13l4 4L19 7"
              stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"
            />
          </svg>
        </button>
        <div
          v-if="message.role === 'assistant' && tokenTotal > 0"
          class="usage-summary usage-summary-static"
          title="本轮总 token"
        >
          <span class="usage-icon" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path
                d="M4 19V5M4 19h16M8 15V9m4 6V7m4 8v-4"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <span class="usage-total">
            {{ formatNum(tokenTotal) }}
            <span class="usage-unit">tokens</span>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import type { Message } from '../types'
import { renderMarkdown } from '../utils/markdown'
import ChartBlock from './ChartBlock.vue'

const props = defineProps<{
  message: Message
  isStreaming?: boolean
  status?: string
  approvalBusy?: boolean
}>()

const emit = defineEmits<{
  decide: [approved: boolean, editedArgs?: Record<string, string>]
}>()

const displayStatus = computed(() => (props.status || '').trim() || '正在思考…')
const tokenTotal = computed(() => props.message.usage?.total_tokens ?? 0)
const hasApprovalCard = computed(() => !!props.message.approval)
const showApprovalDraft = computed(
  () =>
    !!props.message.approval?.draft ||
    props.message.approval?.action === 'send_email',
)

const draftTo = ref('')
const draftSubject = ref('')
const draftBody = ref('')

watch(
  () => props.message.approval,
  (approval) => {
    if (!approval?.draft) {
      draftTo.value = ''
      draftSubject.value = ''
      draftBody.value = ''
      return
    }
    draftTo.value = approval.draft.to || ''
    draftSubject.value = approval.draft.subject || ''
    draftBody.value = approval.draft.body || ''
  },
  { immediate: true, deep: true },
)

function onConfirmApproval() {
  if (!showApprovalDraft.value || !props.message.approval?.editable) {
    emit('decide', true)
    return
  }
  emit('decide', true, {
    to: draftTo.value.trim(),
    subject: draftSubject.value.trim(),
    body: draftBody.value.trim(),
  })
}

/** 已有下载卡片时去掉正文里的链接/文件名行，只保留说明文字 */
function stripFileLinkNoise(text: string): string {
  return (text || '')
    .replace(/\[[^\]]*\]\(https?:\/\/[^)]+\)/gi, '')
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/^[📄📦📎]\s*\S.*$/gmu, '')
    .replace(/^[^\S\n]*\S+\.docx[^\S\n]*$/gimu, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function isImageAtt(att: { mime_type?: string; name?: string; url?: string }) {
  const mime = (att.mime_type || '').toLowerCase()
  if (mime.startsWith('image/')) return true
  const name = (att.name || att.url || '').toLowerCase()
  return /\.(png|jpe?g|gif|webp|bmp|svg)(\?|$)/i.test(name)
}

function isAudioAtt(att: { mime_type?: string; name?: string; url?: string }) {
  const mime = (att.mime_type || '').toLowerCase()
  if (mime.startsWith('audio/') || mime === 'audio/mpeg' || mime === 'audio/mp3') return true
  const name = (att.name || att.url || '').toLowerCase()
  return /\.mp3(\?|$)/i.test(name)
}

const imageAttachments = computed(() =>
  (props.message.attachments || []).filter(isImageAtt),
)
const audioAttachments = computed(() =>
  (props.message.attachments || []).filter((a) => isAudioAtt(a) && !isImageAtt(a)),
)
const fileAttachments = computed(() =>
  (props.message.attachments || []).filter(
    (a) =>
      !isImageAtt(a) &&
      !isAudioAtt(a) &&
      a.kind !== 'chart' &&
      (a.mime_type || '').toLowerCase() !== 'application/x-echarts' &&
      a.kind !== 'xhs_card' &&
      (a.mime_type || '').toLowerCase() !== 'application/x-xhs-card',
  ),
)
const xhsCards = computed(() =>
  [...(props.message.xhsCards || [])]
    .filter((c) => c && c.index > 0)
    .sort((a, b) => a.index - b.index),
)
const chartBlocks = computed(() =>
  (props.message.charts || []).filter((c) => c && c.option && typeof c.option === 'object'),
)

function fileIcon(att: { name?: string; mime_type?: string }) {
  const name = (att.name || '').toLowerCase()
  const mime = (att.mime_type || '').toLowerCase()
  if (name.endsWith('.xlsx') || mime.includes('spreadsheet')) return 'XLSX'
  if (name.endsWith('.docx') || mime.includes('wordprocessing')) return 'DOCX'
  if (name.endsWith('.pdf')) return 'PDF'
  return 'FILE'
}

/** 已有正文/卡片/附件时视为可展示流式内容（用于光标与 streaming 样式） */
const hasStreamableBody = computed(
  () =>
    !!(props.message.content || '').trim() ||
    xhsCards.value.length > 0 ||
    chartBlocks.value.length > 0 ||
    imageAttachments.value.length > 0 ||
    audioAttachments.value.length > 0 ||
    fileAttachments.value.length > 0,
)

const displayContent = computed(() => {
  const raw = props.message.content || ''
  if (!fileAttachments.value.length && !imageAttachments.value.length) return raw
  return stripFileLinkNoise(raw)
})

const STREAMING_CURSOR =
  '<span class="streaming-cursor" aria-hidden="true"></span>'

/** 把闪烁光标插进最后一个块级标签内部，避免掉到正文下一行 */
function withStreamingCursor(html: string): string {
  if (!html) return STREAMING_CURSOR
  // 用 match + slice，避免 replace 回调把 offset 误当成捕获组拼进 HTML
  const codeClose = html.match(/<\/code>(\s*<\/pre>[\s\S]*)$/i)
  if (codeClose && codeClose.index != null) {
    const i = codeClose.index
    return html.slice(0, i) + STREAMING_CURSOR + html.slice(i)
  }
  const blockClose = html.match(/<\/(?:p|h[1-6]|li|blockquote|td|th)>\s*$/i)
  if (blockClose && blockClose.index != null) {
    const i = blockClose.index
    return html.slice(0, i) + STREAMING_CURSOR + html.slice(i)
  }
  // 列表/包裹层以 </ol></ul></div> 收尾时，尽量贴在最后一个 </li> 后
  const listItem = html.match(/<\/li>(\s*<\/(?:ol|ul)>[\s\S]*)$/i)
  if (listItem && listItem.index != null) {
    const i = listItem.index
    return html.slice(0, i) + STREAMING_CURSOR + html.slice(i)
  }
  return html + STREAMING_CURSOR
}

const renderedHtml = computed(() => {
  if (props.message.role !== 'assistant' || !displayContent.value) return ''
  const html = renderMarkdown(displayContent.value)
  return props.isStreaming ? withStreamingCursor(html) : html
})


const showActions = computed(
  () =>
    (!!displayContent.value ||
      !!(props.message.attachments?.length) ||
      !!(props.message.xhsCards?.length)) &&
    !props.isStreaming &&
    props.message.approval?.status !== 'pending',
)
const outgoingStatus = ref('')
const statusKey = ref(0)
const copied = ref(false)
let wipeTimer: ReturnType<typeof setTimeout> | null = null
let copyTimer: ReturnType<typeof setTimeout> | null = null

function formatNum(n: number) {
  return new Intl.NumberFormat('en-US').format(n || 0)
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
}

async function copyMessage() {
  const text = displayContent.value || props.message.content || ''
  if (!text) return
  await copyText(text)
  copied.value = true
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copied.value = false
  }, 1600)
}

async function onMdClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  const btn = target?.closest?.('.md-code-copy') as HTMLElement | null
  if (!btn) return
  e.preventDefault()
  const block = btn.closest('.md-code-block')
  const codeEl = block?.querySelector('pre code')
  const text = (codeEl?.textContent || '').replace(/\n$/, '')
  if (!text) return
  await copyText(text)
  const label = btn.querySelector('.md-code-copy-label')
  const prev = label?.textContent || '复制'
  btn.classList.add('is-copied')
  if (label) label.textContent = '已复制'
  btn.setAttribute('title', '已复制')
  btn.setAttribute('aria-label', '已复制')
  window.setTimeout(() => {
    btn.classList.remove('is-copied')
    if (label) label.textContent = prev
    btn.setAttribute('title', '复制代码')
    btn.setAttribute('aria-label', '复制代码')
  }, 1600)
}

watch(
  () => props.status,
  (next, prev) => {
    const n = (next || '').trim()
    const p = (prev || '').trim()
    if (!n || n === p) return
    if (p) {
      outgoingStatus.value = p
      if (wipeTimer) clearTimeout(wipeTimer)
      wipeTimer = setTimeout(() => {
        outgoingStatus.value = ''
      }, 420)
    }
    statusKey.value += 1
  },
)
</script>
