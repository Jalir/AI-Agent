<template>
  <main class="chat-main">
    <div v-if="store.initialLoading && hadSavedSession" class="loading-bar"></div>
    <header class="chat-header">
      <div class="header-left">
        <button
          class="menu-btn"
          @click="store.toggleSidebar"
          :title="store.sidebarOpen ? '收起会话列表' : '展开会话列表'"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <path d="M9 3v18" />
            <polyline v-if="store.sidebarOpen" points="16 8 12 12 16 16" />
            <polyline v-else points="12 8 16 12 12 16" />
          </svg>
        </button>
      </div>
      <ChatHeaderTitle :title="headerTitle" />
      <div class="header-actions">
        <div v-if="currentStreaming" class="streaming-indicator">
          <span class="dot"></span>
          {{ currentStatus || '回复中...' }}
        </div>
        <button
          class="clear-btn"
          :class="{ invisible: !store.currentConversation || store.currentConversation.messages.length === 0 }"
          title="清空上下文"
          :disabled="!store.currentConversation || store.currentConversation.messages.length === 0"
          @click="handleClear"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            <line x1="10" y1="11" x2="10" y2="17" />
            <line x1="14" y1="11" x2="14" y2="17" />
          </svg>
        </button>
      </div>
    </header>

    <div class="message-area" ref="messageArea">
      <div class="message-area-inner">
        <div v-if="store.initialLoading && hadSavedSession" class="loading-state">
          <div class="loading-spinner"></div>
          <p class="loading-text">加载对话中...</p>
        </div>
        <div v-else-if="!store.currentConversation" class="welcome">
          <div class="welcome-icon" aria-hidden="true">
            <BrandMark :size="26" />
          </div>
          <h2>LangGraph</h2>
          <p>智能助手，解决你的问题</p>
          <button class="start-chat-btn" @click="handleStartChat">开始对话</button>
        </div>
        <div v-else-if="store.currentConversation.messages.length === 0" class="welcome">
          <div class="welcome-icon" aria-hidden="true">
            <BrandMark :size="26" />
          </div>
        </div>

        <MessageBubble
          v-for="(msg, i) in store.currentConversation?.messages"
          :key="i"
          :message="msg"
          :is-streaming="currentStreaming && i === (store.currentConversation?.messages.length ?? 0) - 1 && msg.role === 'assistant'"
          :status="currentStreaming && i === (store.currentConversation?.messages.length ?? 0) - 1 && msg.role === 'assistant' ? currentStatus : ''"
          :approval-busy="currentApprovalBusy"
          @decide="store.handleUserChoice"
        />
      </div>
    </div>

    <div v-if="store.currentConversation && !store.initialLoading" class="input-area">
      <div class="input-wrapper">
        <div v-if="pendingAttachments.length" class="attach-preview-row">
          <div
            v-for="(item, idx) in pendingAttachments"
            :key="item.id"
            class="attach-preview-item"
            :class="{ 'is-audio': item.kind === 'audio' }"
          >
            <audio
              v-if="item.kind === 'audio'"
              class="attach-audio"
              :src="item.previewUrl"
              controls
              preload="metadata"
            />
            <img v-else :src="item.previewUrl" :alt="item.name" />
            <button
              type="button"
              class="attach-remove"
              title="移除"
              :disabled="inputLocked"
              @click="removePending(idx)"
            >×</button>
            <span v-if="item.uploading" class="attach-uploading">上传中</span>
            <span v-else-if="item.error" class="attach-error" title="上传失败">失败</span>
            <span v-else-if="item.kind === 'audio'" class="attach-audio-name" :title="item.name">
              {{ item.name }}
            </span>
          </div>
        </div>
        <input
          ref="inputRef"
          v-model="inputText"
          type="text"
          class="chat-input"
          :placeholder="inputPlaceholder || '发送消息给 LangGraph'"
          :disabled="inputLocked"
          @keyup.enter="handleSend"
          @paste="onPaste"
        />
        <div class="input-toolbar">
          <div class="intent-pills" role="group" aria-label="意图模式">
            <button
              v-for="opt in intentOptions"
              :key="opt.value"
              type="button"
              class="intent-pill"
              :class="{ active: selectedIntent === opt.value }"
              :disabled="inputLocked"
              :title="opt.title"
              :aria-pressed="selectedIntent === opt.value"
              @click="toggleIntent(opt.value)"
            >
              {{ opt.label }}
            </button>
          </div>
          <div class="input-actions">
            <input
              ref="fileInputRef"
              type="file"
              :accept="fileAccept"
              :multiple="!isSpeechMode"
              hidden
              @change="onFilesSelected"
            />
            <button
              type="button"
              class="voice-btn"
              :class="{ active: voiceListening }"
              :title="voiceButtonTitle"
              :disabled="inputLocked || isSpeechMode || voiceStarting"
              @click="toggleVoiceInput"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
            </button>
            <button
              type="button"
              class="attach-btn"
              :title="attachButtonTitle"
              :disabled="inputLocked || pendingAttachments.length >= maxAttachments"
              @click="fileInputRef?.click()"
            >
              <svg
                v-if="isSpeechMode"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
              <svg
                v-else
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <circle cx="8.5" cy="8.5" r="1.5" />
                <path d="M21 15l-5-5L5 21" />
              </svg>
            </button>
            <button
              v-if="currentStreaming"
              type="button"
              class="stop-btn"
              title="停止回答"
              aria-label="停止回答"
              @click="handleStop"
            >
              <span class="stop-icon" aria-hidden="true"></span>
            </button>
            <button
              v-else
              type="button"
              class="send-btn"
              :disabled="!canSend || inputLocked"
              title="发送"
              aria-label="发送"
              @click="handleSend"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, computed, onBeforeUnmount } from 'vue'
import { useChatStore } from '../stores/chat'
import type { ClientIntent, MessageAttachment } from '../types'
import BrandMark from './BrandMark.vue'
import ChatHeaderTitle from './ChatHeaderTitle.vue'
import MessageBubble from './MessageBubble.vue'
import { RealtimeAsrSession, type RealtimeAsrStatus } from '../utils/realtimeAsr'
import { showAlert, showConfirm } from '@/utils/dialog'

const store = useChatStore()
/** 只跟踪标题字符串，与 messages / md-body 更新解耦 */
const headerTitle = computed(() => store.currentConversation?.title || '新对话')
const inputText = ref('')
const inputRef = ref<HTMLInputElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const messageArea = ref<HTMLElement | null>(null)
/** 选中则强制路由；空则走后端自动分类 */
const selectedIntent = ref<ClientIntent | ''>('')

const voiceStatus = ref<RealtimeAsrStatus>('idle')
const voiceStarting = ref(false)
const voiceAutoSending = ref(false)
let voiceSession: RealtimeAsrSession | null = null
const voiceListening = computed(
  () => voiceStatus.value === 'listening' || voiceStatus.value === 'connecting',
)

interface PendingAttachment {
  id: string
  name: string
  previewUrl: string
  kind: 'image' | 'audio'
  uploading: boolean
  error: string
  attachment?: MessageAttachment
}

const pendingAttachments = ref<PendingAttachment[]>([])
const uploading = computed(() => pendingAttachments.value.some((p) => p.uploading))
const pendingApproval = computed(() =>
  !!store.currentConversation?.messages.some(
    (m) => m.role === 'assistant' && m.approval?.status === 'pending',
  ),
)
/** 仅看当前会话是否忙碌，其它对话并发不影响输入 */
const currentStreaming = computed(() => store.isConversationStreaming(store.currentId))
const currentStatus = computed(() => store.conversationStatus(store.currentId))
const currentApprovalBusy = computed(
  () => !!store.runs[store.currentId || '']?.approvalBusy,
)
const inputLocked = computed(
  () =>
    store.isConversationBusy(store.currentId) ||
    uploading.value ||
    pendingApproval.value,
)
const canSend = computed(() => {
  const readyAtts = pendingAttachments.value.filter((p) => p.attachment && !p.error)
  return !!(inputText.value.trim() || readyAtts.length)
})

const intentOptions: { value: ClientIntent; label: string; title: string }[] = [
  { value: 'rag', label: '知识库', title: '检索知识库回答' },
  { value: 'media_gen', label: '图像制作', title: '单张图片/海报生成' },
  { value: 'image_edit', label: '图像编辑', title: '上传 1～3 张图，按文字描述编辑' },
  { value: 'speech_recognize', label: '录音识别', title: '上传 mp3（≤1小时 / 50MB）转写为文字' },
  { value: 'xhs_pack', label: '图文生成', title: '批量图文生成（文案+配图按序卡片）' },
]

const hadSavedSession = !!localStorage.getItem('langgraph_current_id')
const isSpeechMode = computed(() => selectedIntent.value === 'speech_recognize')
const voiceButtonTitle = computed(() => {
  if (isSpeechMode.value) return '录音识别模式请上传 mp3'
  if (voiceStatus.value === 'listening') return '正在听…停顿约 1 秒将自动发送；再点可取消'
  if (voiceStatus.value === 'connecting' || voiceStarting.value) return '正在连接语音识别…'
  return '语音输入（实时出字，停说自动发送）'
})
const maxAttachments = computed(() => {
  if (selectedIntent.value === 'speech_recognize') return 1
  if (selectedIntent.value === 'image_edit') return 3
  return 4
})
const fileAccept = computed(() =>
  isSpeechMode.value
    ? 'audio/mpeg,audio/mp3,.mp3'
    : 'image/jpeg,image/png,image/webp,image/gif',
)
const attachButtonTitle = computed(() => {
  if (isSpeechMode.value) return '上传 mp3 录音（≤1小时 / 50MB）'
  if (selectedIntent.value === 'image_edit') return '上传 1～3 张参考图'
  return '上传图片'
})
const inputPlaceholder = computed(() => {
  if (pendingApproval.value) return '请先在上方确认或取消操作…'
  if (voiceListening.value) return '正在听，请说话…停顿后将自动发送'
  if (isSpeechMode.value) {
    return '上传 mp3，可补充转写要求后发送…'
  }
  if (selectedIntent.value === 'image_edit') {
    return '上传 1～3 张图，并描述如何编辑…'
  }
  return '发送消息给 LangGraph'
})

async function cancelVoiceInput() {
  const session = voiceSession
  voiceSession = null
  voiceStarting.value = false
  if (session?.isActive) {
    await session.stop({ send: false })
  }
  voiceStatus.value = 'idle'
}

async function autoSendFromVoice(text: string) {
  if (voiceAutoSending.value) return
  voiceAutoSending.value = true
  try {
    // Session 会在 onSentenceEnd 返回 true 后自行 stop
    voiceSession = null
    voiceStatus.value = 'idle'
    const t = text.trim()
    if (!t || store.isConversationBusy(store.currentId) || uploading.value) return
    inputText.value = t
    await handleSend()
  } finally {
    voiceAutoSending.value = false
  }
}

async function toggleVoiceInput() {
  if (inputLocked.value || isSpeechMode.value || voiceStarting.value || voiceAutoSending.value) return
  if (voiceSession?.isActive) {
    await cancelVoiceInput()
    return
  }

  voiceStarting.value = true
  const session = new RealtimeAsrSession({
    onStatus: (s) => {
      voiceStatus.value = s
    },
    onPartial: (text) => {
      inputText.value = text
    },
    onError: (msg) => {
      void showAlert(msg)
    },
    onSentenceEnd: (text) => {
      if (!text.trim()) return false
      void autoSendFromVoice(text)
      return true
    },
  })
  voiceSession = session
  try {
    await session.start()
  } catch {
    voiceSession = null
    voiceStatus.value = 'idle'
  } finally {
    voiceStarting.value = false
  }
}

onBeforeUnmount(() => {
  void cancelVoiceInput()
})
const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const MAX_AUDIO_BYTES = 50 * 1024 * 1024
const MAX_AUDIO_DURATION_SEC = 3600

function scrollToBottom() {
  nextTick(() => {
    if (messageArea.value) {
      messageArea.value.scrollTop = messageArea.value.scrollHeight
    }
  })
}

function clearPending() {
  for (const p of pendingAttachments.value) revokePreview(p.previewUrl)
  pendingAttachments.value = []
}

function toggleIntent(value: ClientIntent) {
  const next = selectedIntent.value === value ? '' : value
  const wasSpeech = selectedIntent.value === 'speech_recognize'
  const willSpeech = next === 'speech_recognize'
  selectedIntent.value = next
  // 图片模式 ↔ 录音模式切换时清空不兼容附件
  if (wasSpeech !== willSpeech) {
    clearPending()
    return
  }
  if (selectedIntent.value === 'image_edit' && pendingAttachments.value.length > 3) {
    const dropped = pendingAttachments.value.splice(3)
    for (const p of dropped) revokePreview(p.previewUrl)
  }
}

async function handleClear() {
  if (!store.currentId) return
  const ok = await showConfirm('确定清空该对话的上下文记录吗？', {
    title: '清空上下文',
    confirmText: '清空',
  })
  if (!ok) return
  store.clearConversation(store.currentId)
}

function handleStartChat() {
  store.newConversation()
}

function revokePreview(url: string) {
  if (url.startsWith('blob:')) URL.revokeObjectURL(url)
}

function removePending(idx: number) {
  const item = pendingAttachments.value[idx]
  if (!item) return
  revokePreview(item.previewUrl)
  pendingAttachments.value.splice(idx, 1)
}

function isMp3File(file: File): boolean {
  const mime = (file.type || '').toLowerCase()
  if (mime === 'audio/mpeg' || mime === 'audio/mp3' || mime === 'audio/x-mpeg') return true
  return file.name.toLowerCase().endsWith('.mp3')
}

function readAudioDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const audio = new Audio()
    audio.preload = 'metadata'
    const cleanup = () => URL.revokeObjectURL(url)
    audio.onloadedmetadata = () => {
      const duration = Number(audio.duration)
      cleanup()
      if (!Number.isFinite(duration) || duration <= 0) {
        reject(new Error('无法读取音频时长'))
        return
      }
      resolve(duration)
    }
    audio.onerror = () => {
      cleanup()
      reject(new Error('无法读取音频文件'))
    }
    audio.src = url
  })
}

function pasteImageExt(mime: string): string {
  const m = (mime || '').toLowerCase()
  if (m.includes('png')) return 'png'
  if (m.includes('webp')) return 'webp'
  if (m.includes('gif')) return 'gif'
  if (m.includes('jpeg') || m.includes('jpg')) return 'jpg'
  return 'png'
}

function normalizePasteImage(file: File): File {
  const name = (file.name || '').trim()
  if (name && name !== 'image.png' && name !== 'image.jpg') return file
  const stamp = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const filename = `paste_${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}_${pad(stamp.getHours())}${pad(stamp.getMinutes())}${pad(stamp.getSeconds())}.${pasteImageExt(file.type)}`
  return new File([file], filename, { type: file.type || 'image/png' })
}

async function addFiles(files: File[]) {
  if (!files.length) return

  const room = maxAttachments.value - pendingAttachments.value.length
  if (room <= 0) {
    await showAlert(`最多只能上传 ${maxAttachments.value} 个附件`)
    return
  }
  const selected = files.slice(0, room)

  for (const file of selected) {
    if (isSpeechMode.value) {
      if (!isMp3File(file)) {
        await showAlert(`${file.name} 不是 mp3，已跳过`)
        continue
      }
      if (file.size > MAX_AUDIO_BYTES) {
        await showAlert(`${file.name} 超过 50MB，已跳过`)
        continue
      }
      try {
        const duration = await readAudioDuration(file)
        if (duration > MAX_AUDIO_DURATION_SEC) {
          await showAlert(`${file.name} 时长超过 1 小时，已跳过`)
          continue
        }
      } catch {
        await showAlert(`${file.name} 无法读取时长，已跳过`)
        continue
      }
    } else {
      if (!file.type.startsWith('image/')) continue
      if (file.size > MAX_IMAGE_BYTES) {
        await showAlert(`${file.name} 超过 10MB，已跳过`)
        continue
      }
    }

    const id = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
    const previewUrl = URL.createObjectURL(file)
    pendingAttachments.value.push({
      id,
      name: file.name,
      previewUrl,
      kind: isSpeechMode.value ? 'audio' : 'image',
      uploading: true,
      error: '',
    })
    const item = () => pendingAttachments.value.find((p) => p.id === id)
    try {
      const att = await store.uploadChatImage(file)
      const row = item()
      if (row) {
        row.attachment = att
        row.uploading = false
      }
    } catch {
      const row = item()
      if (row) {
        row.uploading = false
        row.error = '上传失败'
      }
    }
  }
}

async function onFilesSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  await addFiles(files)
}

function onPaste(e: ClipboardEvent) {
  if (inputLocked.value || isSpeechMode.value) return
  const items = e.clipboardData?.items
  if (!items?.length) return

  const images: File[] = []
  for (const item of Array.from(items)) {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) continue
    const file = item.getAsFile()
    if (file) images.push(normalizePasteImage(file))
  }
  if (!images.length) return

  e.preventDefault()
  void addFiles(images)
}

async function handleSend() {
  if (!canSend.value || store.isConversationBusy(store.currentId) || uploading.value) return
  if (pendingAttachments.value.some((p) => p.error)) {
    await showAlert(
      isSpeechMode.value ? '请先移除上传失败的音频' : '请先移除上传失败的图片',
    )
    return
  }
  if (isSpeechMode.value) {
    const ready = pendingAttachments.value.filter((p) => p.attachment && !p.error)
    if (!ready.length) {
      await showAlert('请先上传 mp3 录音文件')
      return
    }
  }
  const text = inputText.value
  const atts = pendingAttachments.value
    .map((p) => p.attachment)
    .filter((a): a is MessageAttachment => !!a)
  inputText.value = ''
  clearPending()
  store.sendMessage(text, 0, selectedIntent.value || undefined, atts)
  scrollToBottom()
}

function handleStop() {
  void store.stopGeneration()
}

function focusInput() {
  nextTick(() => {
    if (inputLocked.value) return
    inputRef.value?.focus()
  })
}

watch(
  () => store.currentConversation?.messages.length,
  () => scrollToBottom()
)

watch(
  () => store.currentId,
  () => {
    scrollToBottom()
    focusInput()
  }
)

// AI 回复结束 / 解锁输入后，自动聚焦输入框，免再点一次
watch(inputLocked, (locked, wasLocked) => {
  if (wasLocked && !locked) focusInput()
})
</script>
