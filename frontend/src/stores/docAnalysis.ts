import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { apiFetch } from '@/api/http'
import type { Message, MessageAttachment, SSEPayload } from '@/types'
import { useAuthStore } from '@/stores/auth'
import { errorFromResponse, toUserError } from '@/utils/safeError'

export type WorkspaceFileStatus = 'pending' | 'parsing' | 'done' | 'failed'

export interface WorkspaceFile {
  id: number
  workspace_id: number
  file_name: string
  file_url: string
  object_key: string
  file_size: number
  file_type: string
  parse_status: WorkspaceFileStatus
  parse_error: string
  char_count: number
  created_at: string
}

export interface WorkspaceMeta {
  id: number
  user_id: number
  title: string
  thread_id: string
  milvus_collection?: string
  status: string
  expires_at: string
}

export const useDocAnalysisStore = defineStore('docAnalysis', () => {
  const ownerUserId = ref<number | null>(null)
  const workspace = ref<WorkspaceMeta | null>(null)
  const files = ref<WorkspaceFile[]>([])
  const messages = ref<Message[]>([])
  const input = ref('')
  const loading = ref(false)
  const streaming = ref(false)
  const status = ref('')
  const error = ref('')
  const uploading = ref(false)
  const ready = ref(false)
  const approvalBusy = ref(false)
  const clearingChat = ref(false)

  let abortController: AbortController | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null

  const pendingApproval = computed(() =>
    messages.value.some((m) => m.role === 'assistant' && m.approval?.status === 'pending'),
  )
  const busy = computed(
    () =>
      loading.value ||
      streaming.value ||
      uploading.value ||
      approvalBusy.value ||
      pendingApproval.value ||
      clearingChat.value,
  )
  const hasReadyFile = computed(() => files.value.some((f) => f.parse_status === 'done'))
  const parsingFiles = computed(() =>
    files.value.some((f) => f.parse_status === 'parsing' || f.parse_status === 'pending'),
  )
  const threadId = computed(() => workspace.value?.thread_id || '')
  const workspaceId = computed(() => workspace.value?.id ?? null)

  function assistantHasBody(m: Message | undefined): boolean {
    if (!m) return false
    return !!(
      (m.content || '').trim() ||
      m.attachments?.length ||
      m.charts?.length ||
      m.xhsCards?.length ||
      m.approval
    )
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function startPollingIfNeeded() {
    stopPolling()
    if (!parsingFiles.value || !workspaceId.value) return
    pollTimer = setInterval(() => {
      void refreshFiles()
    }, 2000)
  }

  function resetAll() {
    abortController?.abort()
    abortController = null
    stopPolling()
    workspace.value = null
    files.value = []
    messages.value = []
    input.value = ''
    loading.value = false
    streaming.value = false
    status.value = ''
    error.value = ''
    uploading.value = false
    ready.value = false
    approvalBusy.value = false
    clearingChat.value = false
    ownerUserId.value = null
  }

  async function ensureUserScope(): Promise<boolean> {
    const auth = useAuthStore()
    const uid = auth.user?.id ?? null
    if (uid == null) {
      if (ownerUserId.value != null || workspace.value) resetAll()
      return false
    }
    if (ownerUserId.value !== uid) {
      resetAll()
      ownerUserId.value = uid
    }
    return true
  }

  async function ensureWorkspace() {
    if (!(await ensureUserScope())) {
      error.value = '请先登录'
      return
    }
    error.value = ''
    try {
      const res = await apiFetch('/api/doc-workspace/ensure', { method: 'POST' })
      if (!res.ok) throw new Error(await errorFromResponse(res, '无法打开文档分析'))
      const data = (await res.json()) as {
        workspace: WorkspaceMeta
        files: WorkspaceFile[]
      }
      workspace.value = data.workspace
      files.value = data.files || []
      ready.value = true
      // 恢复历史消息
      if (data.workspace.thread_id) {
        await loadMessages(data.workspace.thread_id)
      }
      startPollingIfNeeded()
    } catch (err) {
      error.value = toUserError(err, '无法打开文档分析')
      ready.value = false
    }
  }

  async function loadMessages(tid: string) {
    try {
      const res = await apiFetch(`/api/conversations/${encodeURIComponent(tid)}/messages`)
      if (!res.ok) return
      const rows = (await res.json()) as Array<{
        role: string
        content: string
        attachments?: MessageAttachment[]
      }>
      messages.value = (rows || [])
        .filter((r) => r.role === 'user' || r.role === 'assistant' || r.role === 'system')
        .map((r) => ({
          role: r.role as Message['role'],
          content: r.content || '',
          attachments: r.attachments,
        }))
    } catch {
      /* ignore */
    }
  }

  async function refreshFiles() {
    if (!workspaceId.value) return
    try {
      const res = await apiFetch(`/api/doc-workspace/${workspaceId.value}`)
      if (!res.ok) return
      const data = (await res.json()) as { files: WorkspaceFile[] }
      files.value = data.files || []
      if (!parsingFiles.value) stopPolling()
      else if (!pollTimer) startPollingIfNeeded()
    } catch {
      /* ignore */
    }
  }

  async function upload(file: File) {
    if (!workspaceId.value || uploading.value) return
    uploading.value = true
    error.value = ''
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await apiFetch(`/api/doc-workspace/${workspaceId.value}/upload`, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) throw new Error(await errorFromResponse(res, '上传失败'))
      const row = (await res.json()) as WorkspaceFile
      files.value = [row, ...files.value.filter((f) => f.id !== row.id)]
      startPollingIfNeeded()
    } catch (err) {
      error.value = toUserError(err, '上传失败')
    } finally {
      uploading.value = false
    }
  }

  async function removeFile(fileId: number) {
    if (!workspaceId.value || busy.value) return
    error.value = ''
    try {
      const res = await apiFetch(
        `/api/doc-workspace/${workspaceId.value}/files/${fileId}`,
        { method: 'DELETE' },
      )
      if (!res.ok) throw new Error(await errorFromResponse(res, '删除失败'))
      files.value = files.value.filter((f) => f.id !== fileId)
    } catch (err) {
      error.value = toUserError(err, '删除失败')
    }
  }

  function ensureAssistant(): Message {
    let last = messages.value[messages.value.length - 1]
    if (!last || last.role !== 'assistant') {
      messages.value.push({ role: 'assistant', content: '' })
      last = messages.value[messages.value.length - 1]
    }
    return last
  }

  function applySse(payload: SSEPayload, sink?: Message) {
    const last = sink || ensureAssistant()

    if (payload.type === 'status' && payload.content) {
      status.value = payload.content
    } else if (payload.type === 'text' && payload.content) {
      if (status.value) status.value = ''
      last.content = (last.content || '') + payload.content
    } else if (payload.type === 'error' && payload.content) {
      last.content = payload.content
      last.role = 'system'
    } else if (payload.type === 'file' && payload.url) {
      const att: MessageAttachment = {
        url: payload.url,
        object_key: payload.object_key,
        mime_type: payload.mime_type,
        name: payload.name,
      }
      last.attachments = [...(last.attachments || []), att]
    } else if (payload.type === 'usage' && typeof payload.total_tokens === 'number') {
      last.usage = { total_tokens: payload.total_tokens }
    } else if (payload.type === 'interrupt') {
      last.approval = {
        status: 'pending',
        question: payload.data?.question || '是否确认执行？',
        action: payload.data?.action,
        draft: payload.data?.draft,
        editable: payload.data?.editable,
        fields: payload.data?.fields,
      }
      status.value = ''
    }
  }

  async function parseSse(response: Response, signal: AbortSignal, sink?: Message) {
    const reader = response.body?.getReader()
    if (!reader) throw new Error('浏览器不支持流式读取')
    const decoder = new TextDecoder()
    let buffer = ''

    const onAbort = () => {
      void reader.cancel().catch(() => {})
    }
    if (signal.aborted) onAbort()
    else signal.addEventListener('abort', onAbort, { once: true })

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          buffer += decoder.decode()
          break
        }
        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() || ''
        for (const chunk of chunks) {
          const line = chunk
            .split('\n')
            .map((l) => l.trimEnd())
            .find((l) => l.startsWith('data: '))
          if (!line) continue
          try {
            applySse(JSON.parse(line.slice(6)) as SSEPayload, sink)
          } catch {
            /* ignore */
          }
        }
      }
    } finally {
      signal.removeEventListener('abort', onAbort)
    }
  }

  async function sendMessage(text?: string) {
    const trimmed = (text ?? input.value).trim()
    if (!trimmed || !workspace.value || streaming.value || approvalBusy.value || pendingApproval.value) {
      return
    }
    if (!hasReadyFile.value) {
      error.value = '请先上传并等待材料索引完成'
      return
    }

    input.value = ''
    error.value = ''
    messages.value.push({ role: 'user', content: trimmed })
    messages.value.push({ role: 'assistant', content: '' })

    abortController?.abort()
    const ac = new AbortController()
    abortController = ac
    loading.value = true
    streaming.value = true
    status.value = '正在检索材料…'

    try {
      const res = await apiFetch(
        '/api/chat',
        {
          method: 'POST',
          signal: ac.signal,
          body: JSON.stringify({
            thread_id: workspace.value.thread_id,
            message: trimmed,
            intent: 'rag',
            workspace_id: workspace.value.id,
          }),
        },
        { retry: false },
      )
      if (!res.ok || !res.body) {
        throw new Error(await errorFromResponse(res, '发送失败'))
      }
      await parseSse(res, ac.signal)
    } catch (err) {
      const aborted =
        (err instanceof DOMException && err.name === 'AbortError') ||
        (err instanceof Error && err.name === 'AbortError')
      const last = messages.value[messages.value.length - 1]
      if (aborted) {
        if (last?.role === 'assistant' && !assistantHasBody(last)) {
          last.content = '已停止回答'
        }
      } else if (last?.role === 'assistant' && !assistantHasBody(last)) {
        last.content = toUserError(err, '请求失败，请重试')
        last.role = 'system'
      } else {
        error.value = toUserError(err, '请求失败')
      }
    } finally {
      if (abortController === ac) {
        abortController = null
        loading.value = false
        streaming.value = false
        status.value = ''
      }
    }
  }

  async function stopGeneration() {
    if (!streaming.value || !workspace.value) return
    void apiFetch('/api/chat/stop', {
      method: 'POST',
      body: JSON.stringify({ thread_id: workspace.value.thread_id }),
    }).catch(() => {})
    abortController?.abort()
  }

  async function handleUserChoice(
    isApproved: boolean,
    editedArgs?: Record<string, string>,
  ) {
    if (!workspace.value || approvalBusy.value || streaming.value) return
    const target = [...messages.value]
      .reverse()
      .find((m) => m.role === 'assistant' && m.approval?.status === 'pending')
    if (!target) return

    target.approval = undefined
    approvalBusy.value = true
    loading.value = true
    streaming.value = true
    status.value = isApproved ? '正在继续…' : '正在取消…'

    abortController?.abort()
    const ac = new AbortController()
    abortController = ac
    try {
      const res = await apiFetch(
        '/api/approve',
        {
          method: 'POST',
          signal: ac.signal,
          body: JSON.stringify({
            thread_id: workspace.value.thread_id,
            approved: isApproved,
            reason: '',
            edited_args: editedArgs || undefined,
            workspace_id: workspace.value.id,
          }),
        },
        { retry: false },
      )
      if (!res.ok || !res.body) {
        throw new Error(await errorFromResponse(res, '继续执行失败'))
      }
      await parseSse(res, ac.signal, target)
      if (!assistantHasBody(target)) {
        target.content = isApproved ? '已处理完成' : '任务已取消'
      }
    } catch (err) {
      const aborted =
        (err instanceof DOMException && err.name === 'AbortError') ||
        (err instanceof Error && err.name === 'AbortError')
      if (!aborted && !assistantHasBody(target)) {
        target.content = isApproved
          ? toUserError(err, '继续执行失败，请重试')
          : '任务已取消'
      } else if (aborted && !assistantHasBody(target)) {
        target.content = '已停止回答'
      }
    } finally {
      if (abortController === ac) {
        abortController = null
        approvalBusy.value = false
        loading.value = false
        streaming.value = false
        status.value = ''
      } else {
        approvalBusy.value = false
      }
    }
  }

  async function clearChat() {
    if (!workspace.value || clearingChat.value) return
    if (!messages.value.length) return

    // 先停流，再清库
    if (streaming.value || approvalBusy.value) {
      void apiFetch('/api/chat/stop', {
        method: 'POST',
        body: JSON.stringify({ thread_id: workspace.value.thread_id }),
      }).catch(() => {})
      abortController?.abort()
    }

    clearingChat.value = true
    error.value = ''
    try {
      const res = await apiFetch(
        `/api/doc-workspace/${workspace.value.id}/clear-chat`,
        { method: 'POST' },
      )
      if (!res.ok) throw new Error(await errorFromResponse(res, '清空对话失败'))
      messages.value = []
      status.value = ''
      input.value = ''
    } catch (err) {
      error.value = toUserError(err, '清空对话失败')
    } finally {
      clearingChat.value = false
      loading.value = false
      streaming.value = false
      approvalBusy.value = false
      abortController = null
    }
  }

  return {
    workspace,
    files,
    messages,
    input,
    loading,
    streaming,
    status,
    error,
    uploading,
    ready,
    approvalBusy,
    pendingApproval,
    busy,
    hasReadyFile,
    parsingFiles,
    threadId,
    workspaceId,
    ensureUserScope,
    ensureWorkspace,
    refreshFiles,
    upload,
    removeFile,
    sendMessage,
    stopGeneration,
    handleUserChoice,
    clearChat,
    clearingChat,
    resetAll,
    stopPolling,
  }
})
