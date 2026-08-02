import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { KBFile } from '@/types'
import { apiFetch } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { showAlert } from '@/utils/dialog'
import { errorFromResponse, toUserError } from '@/utils/safeError'

const POLL_INTERVAL_MS = 2000

export const useKnowledgeBaseStore = defineStore('knowledgeBase', () => {
  const files = ref<KBFile[]>([])
  const loading = ref(false)
  const initialLoading = ref(true)
  const showUploadModal = ref(false)
  const uploading = ref(false)
  const uploadProgress = ref('')

  let pollTimer: ReturnType<typeof setInterval> | null = null

  const hasParsingFiles = computed(() =>
    files.value.some((f) => f.parse_status === 'parsing'),
  )

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function startPolling() {
    if (pollTimer) return
    pollTimer = setInterval(async () => {
      await fetchFiles({ silent: true })
      if (!hasParsingFiles.value) {
        stopPolling()
      }
    }, POLL_INTERVAL_MS)
  }

  function syncPolling() {
    if (hasParsingFiles.value) {
      startPolling()
    } else {
      stopPolling()
    }
  }

  async function fetchFiles(options: { silent?: boolean } = {}) {
    if (!options.silent) {
      loading.value = true
    }
    try {
      const res = await apiFetch('/api/knowledge/files')
      if (res.ok) {
        files.value = await res.json()
        syncPolling()
      }
    } catch (err) {
      console.error('获取知识库文件列表失败', err)
    } finally {
      if (!options.silent) {
        loading.value = false
      }
      initialLoading.value = false
    }
  }

  async function uploadFiles(fileList: File[]) {
    if (fileList.length === 0) return
    const auth = useAuthStore()
    if (!auth.isAdmin) {
      await showAlert('仅管理员可以上传知识库文件')
      return
    }

    uploading.value = true

    let successCount = 0
    const errors: string[] = []

    for (const file of fileList) {
      uploadProgress.value = `正在上传: ${file.name}（${successCount + 1}/${fileList.length}）`

      try {
        const formData = new FormData()
        formData.append('file', file)

        const res = await apiFetch('/api/knowledge/upload', {
          method: 'POST',
          body: formData,
        })
        if (!res.ok) {
          throw new Error(await errorFromResponse(res, '上传失败，请稍后重试。'))
        }

        const row = (await res.json()) as KBFile
        // 立即插入列表顶部，无需等全部传完再刷新
        files.value = [row, ...files.value.filter((f) => f.id !== row.id)]
        syncPolling()
        successCount++
      } catch (err) {
        const msg = `${file.name}: ${toUserError((err as Error).message, '上传失败')}`
        console.error('文件上传失败', file.name)
        errors.push(msg)
      }
    }

    uploading.value = false
    uploadProgress.value = ''

    if (errors.length > 0) {
      await showAlert(`上传完成: ${successCount} 个成功, ${errors.length} 个失败`)
    }
  }

  async function deleteFile(id: number) {
    const auth = useAuthStore()
    if (!auth.isAdmin) {
      await showAlert('仅管理员可以删除知识库文件')
      return
    }
    try {
      const res = await apiFetch(`/api/knowledge/files/${id}`, {
        method: 'DELETE',
      })
      if (res.ok) {
        files.value = files.value.filter((f) => f.id !== id)
        syncPolling()
      } else {
        await showAlert(await errorFromResponse(res, '删除失败'))
      }
    } catch (err) {
      console.error('删除文件失败', err)
    }
  }

  async function openUploadModal() {
    const auth = useAuthStore()
    if (!auth.isAdmin) {
      await showAlert('仅管理员可以上传知识库文件')
      return
    }
    showUploadModal.value = true
  }

  function closeUploadModal() {
    showUploadModal.value = false
  }

  return {
    files,
    loading,
    initialLoading,
    showUploadModal,
    uploading,
    uploadProgress,
    hasParsingFiles,
    fetchFiles,
    uploadFiles,
    deleteFile,
    openUploadModal,
    closeUploadModal,
    stopPolling,
  }
})
