<template>
  <main class="kb-main">
    <header class="kb-header">
      <div class="kb-header-left">
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
        <h1 class="kb-title">知识库管理</h1>
        <span class="kb-count" v-if="store.files.length">{{ store.files.length }} 个文件</span>
      </div>
      <button v-if="auth.isAdmin" class="kb-create-btn" @click="store.openUploadModal()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        <span>上传文件</span>
      </button>
    </header>

    <div class="kb-body">
      <div v-if="store.uploading" class="upload-progress-bar">{{ store.uploadProgress }}</div>

      <!-- Loading -->
      <div v-if="store.initialLoading" class="kb-empty">
        <div class="loading-spinner"></div>
        <p>加载知识库...</p>
      </div>

      <!-- Empty state -->
      <div v-else-if="store.files.length === 0" class="kb-empty">
        <div class="kb-empty-icon">
          <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            <line x1="12" y1="9" x2="12" y2="15" />
            <line x1="9" y1="12" x2="15" y2="12" />
          </svg>
        </div>
        <h3>{{ store.uploading ? '正在上传...' : '暂无文件' }}</h3>
        <p v-if="store.uploading">文件上传成功后将出现在列表中</p>
        <p v-else-if="auth.isAdmin">上传 PDF 或 DOCX 文档，构建共享知识库。</p>
        <p v-else>知识库由管理员维护，当前暂无文档。</p>
        <button
          v-if="!store.uploading && auth.isAdmin"
          class="kb-empty-btn"
          @click="store.openUploadModal()"
        >上传第一个文件</button>
      </div>

      <!-- File list -->
      <div v-else class="kb-file-table">
        <div class="kb-file-header">
          <span class="kb-file-header-name">文件名</span>
          <span class="kb-file-header-type">类型</span>
          <span class="kb-file-header-status">状态</span>
          <span class="kb-file-header-size">大小</span>
          <span class="kb-file-header-date">上传时间</span>
          <span class="kb-file-header-actions"></span>
        </div>
        <div
          v-for="file in store.files"
          :key="file.id"
          class="kb-file-row"
        >
          <div class="kb-file-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
          </div>
          <a class="kb-file-name" :href="file.file_url" target="_blank" :title="file.file_url">
            {{ file.file_name }}
          </a>
          <span class="kb-file-type">
            <span class="file-type-badge" :class="fileTypeClass(file.file_type)">{{ fileTypeLabel(file.file_type) }}</span>
          </span>
          <span class="kb-file-status">
            <span
              class="parse-status-badge"
              :class="parseStatusClass(file.parse_status)"
              :title="file.parse_status === 'failed' ? '解析失败' : undefined"
            >
              <span v-if="file.parse_status === 'parsing'" class="parse-status-dot"></span>
              {{ parseStatusLabel(file.parse_status) }}
            </span>
          </span>
          <span class="kb-file-size">{{ formatSize(file.file_size) }}</span>
          <span class="kb-file-date">{{ formatDate(file.created_at) }}</span>
          <button
            v-if="auth.isAdmin"
            class="kb-file-delete"
            title="删除文件"
            @click="handleDelete(file)"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- Upload Modal -->
    <Teleport to="body">
      <div v-if="store.showUploadModal" class="modal-overlay" @click.self="handleCancelUpload">
        <div class="modal-card">
          <h3>上传文件</h3>
          <div
            class="upload-dropzone"
            :class="{ dragover: isDragover }"
            @dragover.prevent="isDragover = true"
            @dragleave.prevent="isDragover = false"
            @drop.prevent="handleDrop"
            @click="triggerFileInput"
          >
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <p>拖拽文件到此处，或点击选择文件</p>
            <span class="upload-hint">仅支持 PDF、DOCX 格式</span>
          </div>
          <div v-if="pendingFiles.length > 0" class="upload-pending">
            <div class="upload-pending-title">待上传 ({{ pendingFiles.length }})</div>
            <div v-for="(f, i) in pendingFiles" :key="i" class="upload-pending-item">
              <span class="upload-pending-name">{{ f.name }}</span>
              <span class="upload-pending-size">{{ formatSize(f.size) }}</span>
            </div>
          </div>
          <input
            ref="fileInputRef"
            type="file"
            multiple
            class="upload-input-hidden"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            @change="handleFileSelect"
          />
          <div class="modal-actions">
            <button class="btn-cancel" @click="handleCancelUpload" :disabled="store.uploading">取消</button>
            <button
              class="btn-confirm"
              :disabled="pendingFiles.length === 0 || store.uploading"
              @click="handleUpload"
            >
              <template v-if="store.uploading">
                <span class="uploading-spinner"></span>
                上传中...
              </template>
              <template v-else>上传 ({{ pendingFiles.length }})</template>
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </main>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { useKnowledgeBaseStore } from '../stores/knowledgeBase'
import type { KBFile, ParseStatus } from '@/types'
import { showAlert, showConfirm } from '@/utils/dialog'

const auth = useAuthStore()
const chatStore = useChatStore()
const store = useKnowledgeBaseStore()

const fileInputRef = ref<HTMLInputElement | null>(null)
const isDragover = ref(false)
const pendingFiles = ref<File[]>([])

const ALLOWED_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
]
const ALLOWED_EXTENSIONS = ['.pdf', '.docx']

onMounted(() => {
  store.fetchFiles()
})

onUnmounted(() => {
  store.stopPolling()
})

function triggerFileInput() {
  fileInputRef.value?.click()
}

async function validateFile(file: File): Promise<boolean> {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!ALLOWED_EXTENSIONS.includes(ext) && !ALLOWED_TYPES.includes(file.type)) {
    await showAlert(`不支持的文件格式: ${file.name}，仅支持 PDF 和 DOCX 文件。`)
    return false
  }
  return true
}

async function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement
  if (target.files) {
    for (const f of Array.from(target.files)) {
      if (await validateFile(f)) {
        pendingFiles.value.push(f)
      }
    }
    target.value = ''
  }
}

async function handleDrop(e: DragEvent) {
  isDragover.value = false
  if (e.dataTransfer?.files) {
    for (const f of Array.from(e.dataTransfer.files)) {
      if (await validateFile(f)) {
        pendingFiles.value.push(f)
      }
    }
  }
}

async function handleUpload() {
  if (pendingFiles.value.length === 0 || store.uploading) return
  const filesToUpload = [...pendingFiles.value]
  pendingFiles.value = []
  // 立刻关闭弹窗，列表由 store 在每个文件上传成功后即时插入
  store.closeUploadModal()
  void store.uploadFiles(filesToUpload)
}

function handleCancelUpload() {
  if (store.uploading) return
  pendingFiles.value = []
  store.closeUploadModal()
}

async function handleDelete(file: KBFile) {
  const ok = await showConfirm(
    `确定删除文件「${file.file_name}」吗？此操作不可撤销。`,
    { title: '删除文件', confirmText: '删除' },
  )
  if (ok) store.deleteFile(file.id)
}

function fileTypeClass(type: string): string {
  if (type.includes('pdf')) return 'badge-pdf'
  if (type.includes('docx') || type.includes('word')) return 'badge-docx'
  return ''
}

function fileTypeLabel(type: string): string {
  if (type.includes('pdf')) return 'PDF'
  if (type.includes('docx') || type.includes('word')) return 'DOCX'
  return type.split('/')[1] || type
}

function parseStatusClass(status: ParseStatus | string): string {
  if (status === 'parsing') return 'badge-parsing'
  if (status === 'done') return 'badge-done'
  if (status === 'failed') return 'badge-failed'
  return 'badge-parsing'
}

function parseStatusLabel(status: ParseStatus | string): string {
  if (status === 'parsing') return '解析中'
  if (status === 'done') return '解析完成'
  if (status === 'failed') return '解析失败'
  return '解析中'
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 3600000) return `${Math.max(0, Math.floor(diff / 60000))} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(2)} MB`
}
</script>
