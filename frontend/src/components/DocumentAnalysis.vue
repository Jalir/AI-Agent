<template>
  <main class="chat-main">
    <header class="chat-header">
      <button
        v-if="chatStore.isMobile"
        class="menu-btn"
        @click="chatStore.toggleSidebar"
        title="打开导航"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M9 3v18" />
          <polyline points="12 8 16 12 12 16" />
        </svg>
      </button>
      <ChatHeaderTitle title="文档分析" />
      <div class="header-actions">
        <div v-if="store.streaming" class="streaming-indicator">
          <span class="dot"></span>
          {{ store.status || '回复中...' }}
        </div>
        <button
          type="button"
          class="clear-btn"
          :class="{ invisible: !store.messages.length }"
          title="清空所有对话"
          :disabled="!store.messages.length || store.clearingChat || store.uploading"
          @click="handleClearChat"
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

    <!-- 材料条：紧凑一行，贴合主 Chat 风格 -->
    <div
      class="ws-toolbar"
      :class="{ dragover: isDragover }"
      @dragover.prevent="isDragover = true"
      @dragleave.prevent="isDragover = false"
      @drop.prevent="handleDrop"
    >
      <div class="ws-toolbar-inner">
        <div class="ws-materials">
          <button
            type="button"
            class="ws-upload-btn"
            :disabled="store.uploading || !store.ready"
            title="上传 DOCX / TXT / PDF"
            @click="triggerFileInput"
          >
            <span v-if="store.uploading" class="uploading-spinner"></span>
            <svg
              v-else
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            >
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
            <span>{{ store.uploading ? '上传中' : '上传材料' }}</span>
          </button>

          <div class="ws-file-list">
            <div
              v-for="f in store.files"
              :key="f.id"
              class="ws-file-chip"
              :class="f.parse_status"
              :title="f.parse_error || f.file_name"
            >
              <span class="ws-file-name">{{ f.file_name }}</span>
              <span class="ws-file-status">{{ statusLabel(f.parse_status) }}</span>
              <button
                type="button"
                class="ws-file-remove"
                :disabled="store.busy"
                title="移除"
                @click="store.removeFile(f.id)"
              >
                ×
              </button>
            </div>
            <span v-if="!store.files.length" class="ws-materials-hint">
              拖拽或上传当期材料
            </span>
          </div>

          <input
            ref="fileInputRef"
            type="file"
            class="upload-input-hidden"
            accept=".docx,.txt,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
            @change="handleFileSelect"
          />
        </div>
      </div>
    </div>

    <div v-if="store.error" class="ws-error" role="alert">
      <span>{{ store.error }}</span>
      <button type="button" class="ws-error-dismiss" @click="store.error = ''">关闭</button>
    </div>

    <div class="message-area" ref="messageAreaRef">
      <div class="message-area-inner">
        <div v-if="!store.ready" class="loading-state">
          <div class="loading-spinner"></div>
          <p class="loading-text">正在打开文档分析…</p>
        </div>

        <div v-else-if="!store.messages.length" class="welcome">
          <div class="welcome-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          </div>
          <h2>文档分析已就绪</h2>
          <p>上传材料后，可总结、生成报表或提取行动项。仅检索本次材料。</p>
        </div>

        <MessageBubble
          v-for="(msg, i) in store.messages"
          :key="i"
          :message="msg"
          :is-streaming="store.streaming && i === store.messages.length - 1 && msg.role === 'assistant'"
          :status="store.streaming && i === store.messages.length - 1 && msg.role === 'assistant' ? store.status : ''"
          :approval-busy="store.approvalBusy"
          @decide="store.handleUserChoice"
        />
      </div>
    </div>

    <div v-if="store.ready" class="input-area">
      <div class="input-wrapper">
        <input
          v-model="store.input"
          type="text"
          class="chat-input"
          :placeholder="inputPlaceholder"
          :disabled="inputLocked"
          @keyup.enter="onEnter"
        />
        <div class="input-toolbar">
          <div class="intent-pills" aria-hidden="true"></div>
          <div class="input-actions">
            <button
              v-if="store.streaming"
              type="button"
              class="stop-btn"
              title="停止回答"
              aria-label="停止回答"
              @click="store.stopGeneration()"
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
              @click="store.sendMessage()"
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
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import ChatHeaderTitle from '@/components/ChatHeaderTitle.vue'
import MessageBubble from '@/components/MessageBubble.vue'
import { useChatStore } from '@/stores/chat'
import { useDocAnalysisStore } from '@/stores/docAnalysis'
import { showConfirm } from '@/utils/dialog'

const MAX_DOC_BYTES = 20 * 1024 * 1024

const chatStore = useChatStore()
const store = useDocAnalysisStore()

const fileInputRef = ref<HTMLInputElement | null>(null)
const messageAreaRef = ref<HTMLElement | null>(null)
const isDragover = ref(false)

const inputLocked = computed(
  () =>
    store.streaming ||
    store.approvalBusy ||
    store.pendingApproval ||
    !store.ready,
)

const canSend = computed(
  () =>
    !!store.input.trim() &&
    store.hasReadyFile &&
    !store.streaming &&
    !store.approvalBusy &&
    !store.pendingApproval &&
    store.ready,
)

const inputPlaceholder = computed(() => {
  if (!store.hasReadyFile) return '请先上传并等待材料索引完成…'
  if (store.approvalBusy || store.pendingApproval)
    return '请先确认或取消上方操作…'
  return '基于文档材料提问…'
})

onMounted(() => {
  void store.ensureWorkspace()
})

onUnmounted(() => {
  store.stopPolling()
})

watch(
  () => [store.messages.length, store.messages[store.messages.length - 1]?.content],
  async () => {
    await nextTick()
    const el = messageAreaRef.value
    if (el) el.scrollTop = el.scrollHeight
  },
)

function statusLabel(status: string) {
  if (status === 'done') return '已索引'
  if (status === 'parsing' || status === 'pending') return '索引中'
  if (status === 'failed') return '失败'
  return status
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

function isSupported(file: File) {
  const name = file.name.toLowerCase()
  return name.endsWith('.docx') || name.endsWith('.txt') || name.endsWith('.pdf')
}

async function takeFile(file: File | undefined) {
  if (!file || store.uploading) return
  if (!isSupported(file)) {
    store.error = '仅支持 DOCX / TXT / PDF'
    return
  }
  if (file.size > MAX_DOC_BYTES) {
    store.error = '文件超过 20MB'
    return
  }
  await store.upload(file)
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  void takeFile(file)
}

function handleDrop(e: DragEvent) {
  isDragover.value = false
  void takeFile(e.dataTransfer?.files?.[0])
}

function onEnter() {
  if (canSend.value && !inputLocked.value) void store.sendMessage()
}

async function handleClearChat() {
  if (!store.messages.length || store.clearingChat) return
  const ok = await showConfirm(
    '确定清空文档分析全部对话吗？材料文件会保留，对话记录将从数据库删除且不可恢复。',
    { title: '清空对话', confirmText: '清空' },
  )
  if (!ok) return
  void store.clearChat()
}
</script>
