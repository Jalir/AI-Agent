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
          <h1 class="vc-title">声音克隆</h1>
          <p class="vc-subtitle">上传参考音色，用同一声音复述任意文案</p>
        </div>
      </div>
    </header>

    <div class="vc-body">
      <div v-if="store.error" class="vc-error" role="alert">
        <span>{{ store.error }}</span>
        <button type="button" class="vc-error-dismiss" @click="store.error = ''">关闭</button>
      </div>

      <div class="vc-grid">
        <!-- 参考音色 -->
        <section class="vc-panel">
          <div class="vc-panel-head">
            <h2 class="vc-panel-title">参考音色</h2>
            <span class="vc-panel-hint">建议 8–10 秒、单人清晰 mp3；超过 20 秒仅识别前 20 秒</span>
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
              {{ store.phase === 'uploading' ? '正在上传…' : '拖拽或点击上传参考 MP3' }}
            </p>
            <span class="vc-dropzone-sub">上传后自动识别原文（长音频只转写前 20 秒）</span>
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
            <p v-if="store.truncated" class="vc-trim-note">
              长音频已截取：识别与克隆仅使用前 20 秒片段
            </p>
          </div>

          <div class="vc-field-block">
            <div class="vc-field-row">
              <span class="vc-field-label">合成模型</span>
              <div class="vc-model-switch" role="group" aria-label="合成模型">
                <button
                  type="button"
                  :class="['vc-model-btn', { active: store.model === MOSS_MODEL }]"
                  :disabled="store.busy"
                  @click="store.model = MOSS_MODEL"
                >
                  MOSS
                </button>
                <button
                  type="button"
                  :class="['vc-model-btn', { active: store.model === COSY_MODEL }]"
                  :disabled="store.busy"
                  @click="store.model = COSY_MODEL"
                >
                  CosyVoice
                </button>
              </div>
            </div>
          </div>

          <label class="vc-label">
            <span>参考音原文</span>
            <button
              v-if="store.originalAudioUrl || store.audioUrl"
              type="button"
              class="vc-text-btn"
              :disabled="store.busy"
              @click="store.retranscribe()"
            >
              重新识别
            </button>
          </label>
          <textarea
            v-model="store.referenceText"
            class="vc-textarea"
            rows="5"
            :disabled="!(store.originalAudioUrl || store.audioUrl) || store.busy"
            placeholder="上传后自动填充；请核对是否与参考音一致"
          ></textarea>

          <input
            ref="fileInputRef"
            type="file"
            class="upload-input-hidden"
            accept=".mp3,audio/mpeg,audio/mp3"
            @change="handleFileSelect"
          />
        </section>

        <!-- 合成 -->
        <section class="vc-panel">
          <div class="vc-panel-head">
            <h2 class="vc-panel-title">要说的话</h2>
            <span class="vc-panel-hint">用克隆音色复述这段文字</span>
          </div>

          <textarea
            v-model="store.speakText"
            class="vc-textarea vc-textarea-lg"
            rows="8"
            :disabled="store.busy"
            placeholder="输入希望用参考音色说出的内容…"
          ></textarea>

          <div class="vc-speed-row">
            <div class="vc-speed-head">
              <span class="vc-field-label">语速</span>
              <span class="vc-speed-value">{{ store.speed.toFixed(2) }}×</span>
            </div>
            <input
              class="vc-speed-slider"
              type="range"
              min="0.25"
              max="4"
              step="0.05"
              :disabled="store.busy"
              :value="store.speed"
              @input="onSpeedInput"
            />
            <div class="vc-speed-marks">
              <span>0.25</span>
              <span>1.0</span>
              <span>4.0</span>
            </div>
          </div>

          <div class="vc-actions">
            <button
              type="button"
              class="vc-primary-btn"
              :disabled="!store.canSynthesize"
              @click="store.synthesize()"
            >
              <span v-if="store.phase === 'synthesizing'" class="uploading-spinner"></span>
              {{ store.phase === 'synthesizing' ? '正在合成…' : '生成语音' }}
            </button>
          </div>

          <div v-if="store.activeResult" class="vc-result">
            <div class="vc-result-head">
              <h3 class="vc-result-title">当前结果</h3>
              <a
                class="vc-download-btn"
                :href="store.activeResult.audioUrl"
                :download="downloadName(store.activeResult)"
                target="_blank"
                rel="noopener"
              >
                下载 MP3
              </a>
            </div>
            <p class="vc-result-text" :title="store.activeResult.speakText">
              {{ store.activeResult.speakText }}
            </p>
            <div class="vc-result-stage" aria-hidden="true">
              <div class="vc-result-bars">
                <span v-for="n in 24" :key="n" :style="{ animationDelay: `${(n % 8) * 0.08}s` }"></span>
              </div>
            </div>
            <audio
              class="vc-audio"
              controls
              :src="store.activeResult.audioUrl"
            ></audio>
          </div>
        </section>
      </div>

      <section class="vc-history">
        <div class="vc-history-head">
          <div>
            <h2 class="vc-panel-title">合成历史</h2>
            <span class="vc-panel-hint">按用户持久保存，刷新后仍可播放</span>
          </div>
          <button
            v-if="store.history.length"
            type="button"
            class="vc-text-btn"
            :disabled="store.busy || store.historyLoading"
            @click="confirmClearHistory"
          >
            清空
          </button>
        </div>
        <div v-if="store.historyLoading" class="vc-history-empty">加载历史中…</div>
        <div v-else-if="!store.history.length" class="vc-history-empty">暂无合成记录</div>
        <div v-else class="vc-history-list">
          <article
            v-for="item in store.history"
            :key="item.id"
            :class="['vc-history-item', { active: item.id === store.activeResult?.id }]"
            @click="store.selectHistory(item.id)"
          >
            <div class="vc-history-meta">
              <span class="vc-history-time">{{ formatTime(item.createdAt) }}</span>
              <span class="vc-history-badge">{{ store.modelLabel(item.model) }}</span>
              <span class="vc-history-badge">{{ item.speed.toFixed(2) }}×</span>
              <span class="vc-history-ref" :title="item.refFileName">{{ item.refFileName }}</span>
            </div>
            <p class="vc-history-text" :title="item.speakText">{{ item.speakText }}</p>
            <audio
              class="vc-audio"
              controls
              preload="metadata"
              :src="item.audioUrl"
              @click.stop
            ></audio>
            <div class="vc-history-actions">
              <a
                class="vc-download-btn"
                :href="item.audioUrl"
                :download="downloadName(item)"
                target="_blank"
                rel="noopener"
                @click.stop
              >
                下载
              </a>
              <button
                type="button"
                class="vc-text-btn vc-danger-btn"
                @click.stop="store.removeHistory(item.id)"
              >
                删除
              </button>
            </div>
          </article>
        </div>
      </section>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import { showConfirm } from '@/utils/dialog'
import {
  COSY_MODEL,
  MOSS_MODEL,
  useVoiceCloneStore,
  type VoiceCloneHistoryItem,
} from '@/stores/voiceClone'

const chatStore = useChatStore()
const store = useVoiceCloneStore()

const fileInputRef = ref<HTMLInputElement | null>(null)
const isDragover = ref(false)

onMounted(() => {
  void store.ensureUserScope()
})

function downloadName(item: VoiceCloneHistoryItem): string {
  const stamp = new Date(item.createdAt)
  const pad = (n: number) => String(n).padStart(2, '0')
  const name = `voice-clone_${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}_${pad(stamp.getHours())}${pad(stamp.getMinutes())}${pad(stamp.getSeconds())}.mp3`
  return name
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  if (sameDay) return hm
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`
}

async function confirmClearHistory() {
  if (!store.history.length) return
  const ok = await showConfirm(
    '确定清空全部合成历史吗？此操作会删除云端录音，不可撤销。',
    { title: '清空合成历史', confirmText: '清空' },
  )
  if (ok) void store.clearHistory()
}

const phaseLabel = computed(() => {
  if (store.phase === 'uploading') return '上传中…'
  if (store.phase === 'transcribing') {
    return store.truncated ? '识别前 20 秒…' : '识别原文中…'
  }
  if (store.phase === 'synthesizing') return '合成中…'
  return store.truncated ? '已就绪（已截取前 20 秒）' : '已就绪'
})

function onSpeedInput(e: Event) {
  const v = Number((e.target as HTMLInputElement).value)
  store.speed = store.clampSpeed(v)
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

function replaceFile() {
  if (store.busy) return
  triggerFileInput()
}

function validateMp3(file: File): boolean {
  const name = file.name.toLowerCase()
  const ok =
    name.endsWith('.mp3') ||
    file.type === 'audio/mpeg' ||
    file.type === 'audio/mp3'
  if (!ok) {
    store.error = '仅支持 MP3 格式的参考音频'
    return false
  }
  return true
}

async function takeFile(file: File | undefined) {
  if (!file || store.busy) return
  if (!validateMp3(file)) return
  await store.uploadAndTranscribe(file)
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
