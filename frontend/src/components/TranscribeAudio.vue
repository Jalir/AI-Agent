<template>
  <main class="vc-main">
    <header class="vc-header">
      <div class="vc-header-left">
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
        <div class="vc-header-titles">
          <h1 class="vc-title">转录音频</h1>
          <p class="vc-subtitle">上传 MP3，点击开始转写为全文文字</p>
        </div>
      </div>
    </header>

    <div class="vc-body">
      <div v-if="store.error" class="vc-error" role="alert">
        <span>{{ store.error }}</span>
        <button type="button" class="vc-error-dismiss" @click="store.error = ''">关闭</button>
      </div>

      <div class="ta-layout">
        <section class="vc-panel">
          <div class="vc-panel-head">
            <h2 class="vc-panel-title">上传音频</h2>
            <span class="vc-panel-hint">仅支持 MP3，不超过 1 小时 / 50MB；上传后点击下方按钮开始转写</span>
          </div>

          <div
            v-if="!store.displayUrl && !store.audioUrl"
            class="vc-dropzone"
            :class="{ dragover: isDragover, disabled: store.busy }"
            @dragover.prevent="!store.busy && (isDragover = true)"
            @dragleave.prevent="isDragover = false"
            @drop.prevent="handleDrop"
            @click="!store.busy && triggerFileInput()"
          >
            <div class="vc-dropzone-visual" aria-hidden="true">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="23" />
                <line x1="8" y1="23" x2="16" y2="23" />
              </svg>
              <div class="vc-wave">
                <span v-for="n in 7" :key="n"></span>
              </div>
            </div>
            <p class="vc-dropzone-title">
              {{ store.phase === 'uploading' ? '正在上传…' : '拖拽或点击上传 MP3' }}
            </p>
            <span class="vc-dropzone-sub">上传完成后点击「开始转录」</span>
          </div>

          <div v-else class="vc-ref-card">
            <div class="vc-ref-meta">
              <div class="vc-ref-icon" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                  <path d="M9 18V5l12-2v13" />
                  <circle cx="6" cy="18" r="3" />
                  <circle cx="18" cy="16" r="3" />
                </svg>
              </div>
              <div class="vc-ref-info">
                <span class="vc-ref-name" :title="store.fileName">{{ store.fileName }}</span>
                <span class="vc-ref-status">{{ phaseLabel }}</span>
              </div>
              <button
                type="button"
                class="vc-text-btn"
                :disabled="store.busy"
                @click="replaceFile"
              >
                更换
              </button>
            </div>
            <audio
              v-if="store.displayUrl"
              class="vc-audio"
              controls
              :src="store.displayUrl"
              preload="metadata"
            ></audio>
          </div>

          <button
            type="button"
            class="vc-primary-btn ta-start-btn"
            :disabled="!store.canStart"
            @click="store.startTranscribe()"
          >
            <span v-if="store.phase === 'transcribing'" class="uploading-spinner"></span>
            {{ startButtonLabel }}
          </button>

          <input
            ref="fileInputRef"
            type="file"
            class="upload-input-hidden"
            accept=".mp3,audio/mpeg,audio/mp3"
            @change="handleFileSelect"
          />
        </section>

        <section class="vc-panel">
          <div class="vc-panel-head">
            <h2 class="vc-panel-title">转写结果</h2>
            <span class="vc-panel-hint">可直接编辑校对，支持复制与下载</span>
          </div>

          <div class="vc-label">
            <span>全文</span>
            <div class="ta-result-actions">
              <button
                v-if="store.phase === 'transcribing'"
                type="button"
                class="vc-text-btn vc-danger-btn"
                @click="store.cancel()"
              >
                取消
              </button>
              <button
                v-if="store.audioUrl"
                type="button"
                class="vc-text-btn"
                :disabled="store.busy"
                @click="store.retranscribe()"
              >
                重新识别
              </button>
              <button
                type="button"
                class="vc-text-btn"
                :disabled="!store.hasResult || store.busy"
                @click="store.copyText()"
              >
                {{ store.copied ? '已复制' : '复制' }}
              </button>
              <button
                type="button"
                class="vc-text-btn"
                :disabled="!store.hasResult || store.busy"
                @click="store.downloadText()"
              >
                下载 TXT
              </button>
            </div>
          </div>

          <div v-if="store.phase === 'transcribing' || store.progressPercent > 0" class="ta-progress">
            <div class="ta-progress-head">
              <span class="ta-progress-msg">
                <span v-if="store.phase === 'transcribing'" class="uploading-spinner"></span>
                {{ progressLabel }}
              </span>
              <span class="ta-progress-pct">{{ store.progressPercent }}%</span>
            </div>
            <div class="ta-progress-track" role="progressbar" :aria-valuenow="store.progressPercent" aria-valuemin="0" aria-valuemax="100">
              <div class="ta-progress-fill" :style="{ width: `${store.progressPercent}%` }"></div>
            </div>
          </div>

          <textarea
            v-model="store.text"
            class="vc-textarea vc-textarea-lg ta-textarea"
            rows="16"
            :disabled="store.busy && !store.text"
            :placeholder="textareaPlaceholder"
          ></textarea>
        </section>
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useTranscribeStore } from '@/stores/transcribe'

const MAX_AUDIO_BYTES = 50 * 1024 * 1024
const MAX_AUDIO_DURATION_SEC = 3600

const chatStore = useChatStore()
const store = useTranscribeStore()

const fileInputRef = ref<HTMLInputElement | null>(null)
const isDragover = ref(false)

onMounted(() => {
  void store.ensureUserScope()
})

const phaseLabel = computed(() => {
  if (store.phase === 'uploading') return '上传中…'
  if (store.phase === 'transcribing') {
    if (store.progressTotal > 0) {
      return `转写中 ${store.progressCurrent}/${store.progressTotal}`
    }
    return '转写中…'
  }
  return store.hasResult ? '转写完成' : '已上传'
})

const progressLabel = computed(() => {
  if (store.progressMessage) return store.progressMessage
  if (store.phase === 'transcribing') return '正在转写…'
  return store.progressPercent >= 100 ? '转写完成' : '准备中…'
})

const startButtonLabel = computed(() => {
  if (store.phase === 'uploading') return '正在上传…'
  if (store.phase === 'transcribing') return '正在转录…'
  if (!store.audioUrl) return '开始转录'
  return store.hasResult ? '重新转录' : '开始转录'
})

const textareaPlaceholder = computed(() => {
  if (store.phase === 'uploading') return '正在上传音频…'
  if (store.phase === 'transcribing') {
    return store.text ? '正在继续识别后续段落…' : '正在分析并分段识别，结果会逐步出现…'
  }
  if (store.audioUrl) return '点击左侧「开始转录」后，结果会显示在这里'
  return '请先上传 MP3 音频'
})

function triggerFileInput() {
  fileInputRef.value?.click()
}

function replaceFile() {
  if (store.busy) return
  triggerFileInput()
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

async function validateFile(file: File): Promise<boolean> {
  if (!isMp3File(file)) {
    store.error = '仅支持 MP3 格式的音频'
    return false
  }
  if (file.size > MAX_AUDIO_BYTES) {
    store.error = '音频超过 50MB，请压缩后重试'
    return false
  }
  try {
    const duration = await readAudioDuration(file)
    if (duration > MAX_AUDIO_DURATION_SEC) {
      store.error = '音频时长超过 1 小时，请截取后重试'
      return false
    }
  } catch {
    store.error = '无法读取音频时长，请确认文件完整且为有效 MP3'
    return false
  }
  return true
}

async function takeFile(file: File | undefined) {
  if (!file || store.busy) return
  if (!(await validateFile(file))) return
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
  if (store.busy) return
  void takeFile(e.dataTransfer?.files?.[0])
}
</script>
