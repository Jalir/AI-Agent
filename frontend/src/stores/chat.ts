import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { ClientIntent, Conversation, Message, MessageAttachment, SSEPayload } from '@/types'
import { apiFetch } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { errorFromResponse, redactSecrets, toUserError } from '@/utils/safeError'

function currentIdKey(): string {
  const uid = useAuthStore().user?.id
  return uid != null ? `langgraph_current_id_${uid}` : 'langgraph_current_id'
}

let nextConvId = 1

/** 单会话运行态（多对话可并行；互不抢全局锁） */
interface ConvRunUi {
  loading: boolean
  streaming: boolean
  status: string
  approvalBusy: boolean
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>([])
  const currentId = ref<string | null>(null)
  /** 按 conversation id 隔离的 UI 运行态 */
  const runs = ref<Record<string, ConvRunUi>>({})
  const initialLoading = ref(true)
  const initialLoaded = ref(false)
  /** 对话列表拉取失败（与「真的没有对话」区分） */
  const conversationsLoadError = ref(false)

  const activeView = ref<'chat' | 'knowledgeBase' | 'voiceClone' | 'transcribe' | 'docAnalysis' | 'salesAnalysis' | 'userManagement'>('chat')

  /** 每会话 AbortController / 停止标记（不进响应式，避免不可序列化） */
  const abortByConv = new Map<string, AbortController>()
  const stopRequestedByConv = new Map<string, boolean>()

  const STOPPED_REPLY = '已停止回答'

  function _emptyRun(): ConvRunUi {
    return { loading: false, streaming: false, status: '', approvalBusy: false }
  }

  function _ensureRun(convId: string): ConvRunUi {
    if (!runs.value[convId]) {
      runs.value[convId] = _emptyRun()
    }
    return runs.value[convId]
  }

  function _patchRun(convId: string, patch: Partial<ConvRunUi>) {
    const prev = _ensureRun(convId)
    runs.value[convId] = { ...prev, ...patch }
  }

  function _clearRun(convId: string) {
    abortByConv.delete(convId)
    stopRequestedByConv.delete(convId)
    if (runs.value[convId]) {
      const next = { ...runs.value }
      delete next[convId]
      runs.value = next
    }
  }

  function isConversationBusy(convId: string | null | undefined): boolean {
    if (!convId) return false
    const r = runs.value[convId]
    return !!(r && (r.loading || r.streaming || r.approvalBusy))
  }

  function isConversationStreaming(convId: string | null | undefined): boolean {
    if (!convId) return false
    const r = runs.value[convId]
    return !!(r && (r.streaming || r.loading))
  }

  function conversationStatus(convId: string | null | undefined): string {
    if (!convId) return ''
    return runs.value[convId]?.status || ''
  }

  /** 当前会话运行态（供输入区 / 气泡绑定） */
  const loading = computed(() => !!runs.value[currentId.value || '']?.loading)
  const streaming = computed(() => !!runs.value[currentId.value || '']?.streaming)
  const streamingStatus = computed(() => runs.value[currentId.value || '']?.status || '')
  const approvalBusy = computed(() => !!runs.value[currentId.value || '']?.approvalBusy)

  // ---- sidebar: icon rail always on; history panel + mobile drawer ----
  const SIDEBAR_BREAKPOINT = 768
  const isMobile = ref(
    typeof window !== 'undefined' ? window.innerWidth < SIDEBAR_BREAKPOINT : false,
  )
  /** Desktop: history panel open; Mobile: drawer open. Preference kept across mobile visits. */
  let desktopPreferOpen = true
  const sidebarOpen = ref(!isMobile.value)

  function _setOpen(open: boolean) {
    sidebarOpen.value = open
    if (!isMobile.value) desktopPreferOpen = open
  }

  function toggleSidebar() { _setOpen(!sidebarOpen.value) }
  function openSidebar() { _setOpen(true) }
  function closeSidebar() { _setOpen(false) }
  /** Close only in drawer mode so desktop selection does not collapse the panel */
  function closeSidebarIfMobile() {
    if (isMobile.value) closeSidebar()
  }

  function _onViewportChange() {
    const mobile = window.innerWidth < SIDEBAR_BREAKPOINT
    if (mobile === isMobile.value) return
    if (mobile) {
      desktopPreferOpen = sidebarOpen.value
      sidebarOpen.value = false
    } else {
      sidebarOpen.value = desktopPreferOpen
    }
    isMobile.value = mobile
  }

  function initSidebarLayout() {
    isMobile.value = window.innerWidth < SIDEBAR_BREAKPOINT
    sidebarOpen.value = isMobile.value ? false : desktopPreferOpen
    window.addEventListener('resize', _onViewportChange)
  }

  function teardownSidebarLayout() {
    window.removeEventListener('resize', _onViewportChange)
  }

  const currentConversation = computed<Conversation | null>(() =>
    conversations.value.find((c) => c.id === currentId.value) ?? null
  )

  const sortedConversations = computed<Conversation[]>(() =>
    [...conversations.value].sort((a, b) => b.updatedAt - a.updatedAt)
  )

  // ---- local helpers ----

  function _genThreadId(): string {
    return `session_${Date.now().toString(36)}${(nextConvId++).toString(36)}`
  }

  function _getConv(id: string): Conversation | null {
    return conversations.value.find((c) => c.id === id) ?? null
  }

  // ---- actions ----

  function resetLocalState() {
    for (const id of Object.keys(runs.value)) {
      abortByConv.get(id)?.abort()
      _clearRun(id)
    }
    conversations.value = []
    currentId.value = null
    initialLoading.value = true
    initialLoaded.value = false
    conversationsLoadError.value = false
    activeView.value = 'chat'
  }

  async function loadConversations() {
    conversationsLoadError.value = false
    try {
      const res = await apiFetch('/api/conversations')
      if (!res.ok) {
        conversationsLoadError.value = true
        return
      }
      const rows: Array<{
        thread_id: string; title: string; created_at: string; updated_at: string
      }> = await res.json()

      for (const r of rows) {
        const exist = _getConv(r.thread_id)
        if (exist) {
          exist.title = r.title
          exist.updatedAt = new Date(r.updated_at).getTime()
        } else {
          conversations.value.push({
            id: r.thread_id,
            threadId: r.thread_id,
            title: r.title,
            messages: [],
            createdAt: new Date(r.created_at).getTime(),
            updatedAt: new Date(r.updated_at).getTime(),
          })
        }
      }
    } catch (err) {
      console.error('加载对话列表失败', err)
      conversationsLoadError.value = true
    } finally {
      initialLoaded.value = true
    }
  }

  async function loadMessages(threadId: string) {
    const conv = _getConv(threadId)
    if (!conv) return
    try {
      const res = await apiFetch(`/api/conversations/${threadId}/messages`)
      if (!res.ok) return
      const rows: Array<{
        role: string
        content: string
        token_total?: number
        attachments?: Message['attachments']
      }> = await res.json()
      conv.messages = rows.map((r) => {
        const msg: Message = {
          role: r.role as Message['role'],
          content: r.content,
        }
        if (Array.isArray(r.attachments) && r.attachments.length > 0) {
          const cards: NonNullable<Message['xhsCards']> = []
          const charts: NonNullable<Message['charts']> = []
          const files: NonNullable<Message['attachments']> = []
          for (const a of r.attachments) {
            const kind = (a as { kind?: string }).kind
            const mime = (a.mime_type || '').toLowerCase()
            if (kind === 'xhs_card' || mime === 'application/x-xhs-card') {
              const raw = a as Record<string, unknown>
              let index = Number(raw.index) || 0
              if (index <= 0) {
                const name = String(raw.name || a.name || '')
                const m = /^xhs_(\d+)$/i.exec(name)
                if (m) index = Number(m[1]) || 0
              }
              const rawErr = String(raw.error || '').trim()
              cards.push({
                index,
                title: String(raw.title || ''),
                body: String(raw.body || ''),
                tags: Array.isArray(raw.tags) ? raw.tags.map(String) : [],
                image_url: String(raw.image_url || a.url || ''),
                error: rawErr ? toUserError(rawErr, '生成失败，请稍后重试。') : '',
              })
            } else if (kind === 'chart' || mime === 'application/x-echarts') {
              const raw = a as Record<string, unknown>
              const option = raw.option
              if (option && typeof option === 'object') {
                charts.push({
                  chart_id: String(raw.chart_id || ''),
                  title: String(raw.title || a.name || ''),
                  option: option as Record<string, unknown>,
                  evidence:
                    raw.evidence && typeof raw.evidence === 'object'
                      ? (raw.evidence as Record<string, unknown>)
                      : undefined,
                })
              }
            } else {
              files.push({
                url: a.url,
                object_key: a.object_key,
                mime_type: a.mime_type,
                name: a.name,
              })
            }
          }
          if (files.length) msg.attachments = files
          if (cards.length) {
            msg.xhsCards = cards
              .filter((c) => c.index > 0)
              .sort((a, b) => a.index - b.index)
          }
          if (charts.length) msg.charts = charts
        }
        const total = Number(r.token_total) || 0
        if (total > 0 && r.role === 'assistant') {
          msg.usage = { total_tokens: total }
        }
        return msg
      })
    } catch (err) {
      console.error('加载消息失败', err)
    }
  }

  async function restoreCurrentId() {
    const saved = localStorage.getItem(currentIdKey())
    if (saved && conversations.value.some(c => c.id === saved)) {
      currentId.value = saved
      const conv = _getConv(saved)
      if (conv && conv.messages.length === 0) {
        await loadMessages(saved)
      }
    }
  }

  function newConversation(): Conversation {
    const tid = _genThreadId()
    const conv: Conversation = {
      id: tid,
      threadId: tid,
      title: '新对话',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    conversations.value.unshift(conv)
    currentId.value = tid
    return conv
  }

  // persist currentId across refreshes（按用户隔离）
  watch(currentId, (val) => {
    const key = currentIdKey()
    if (val) {
      localStorage.setItem(key, val)
    } else {
      localStorage.removeItem(key)
    }
  })

  async function selectConversation(id: string) {
    currentId.value = id
    const conv = _getConv(id)
    if (conv && conv.messages.length === 0) {
      await loadMessages(id)
    }
  }

  async function deleteConversation(id: string) {
    try {
      const conv = _getConv(id)
      if (conv?.threadId && isConversationStreaming(id)) {
        void apiFetch('/api/chat/stop', {
          method: 'POST',
          body: JSON.stringify({ thread_id: conv.threadId }),
        }).catch(() => {})
      }
      abortByConv.get(id)?.abort()
      _clearRun(id)

      const res = await apiFetch(`/api/conversations/${id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const idx = conversations.value.findIndex((c) => c.id === id)
      if (idx === -1) return
      conversations.value.splice(idx, 1)
      if (currentId.value === id) {
        currentId.value = conversations.value.length > 0 ? conversations.value[0].id : null
      }
    } catch (err) {
      console.error('删除对话失败', err)
    }
  }

  async function deleteAllConversations() {
    try {
      for (const id of Object.keys(runs.value)) {
        abortByConv.get(id)?.abort()
        _clearRun(id)
      }
      const res = await apiFetch('/api/conversations', { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      conversations.value = []
      currentId.value = null
    } catch (err) {
      console.error('清空所有对话失败', err)
    }
  }

  async function clearConversation(id: string) {
    const conv = _getConv(id)
    if (!conv) return
    if (isConversationStreaming(id) && conv.threadId) {
      void apiFetch('/api/chat/stop', {
        method: 'POST',
        body: JSON.stringify({ thread_id: conv.threadId }),
      }).catch(() => {})
      abortByConv.get(id)?.abort()
      _clearRun(id)
    }
    conv.messages = []
    conv.title = '新对话'
    try {
      await apiFetch(`/api/conversations/${id}/clear`, { method: 'POST' })
    } catch { /* ignore */ }
  }

  // ---- SSE ----

  function _applyStoppedReply(convId: string) {
    const conv = _getConv(convId)
    if (!conv) return
    const last = conv.messages[conv.messages.length - 1]
    if (!last || last.role !== 'assistant') return
    const hasBody = !!last.content.trim()
    const hasFiles = !!(last.attachments && last.attachments.length)
    const hasCards = !!(last.xhsCards && last.xhsCards.length)
    // 思考中停下：写入占位文案；已有正文/附件则保留已输出内容
    if (!hasBody && !hasFiles && !hasCards) {
      last.content = STOPPED_REPLY
    }
    conv.updatedAt = Date.now()
  }

  function _assistantSink(conv: Conversation, sink?: Message): Message {
    if (sink && sink.role === 'assistant') return sink
    let last = conv.messages[conv.messages.length - 1]
    if (!last || last.role !== 'assistant') {
      conv.messages.push({ role: 'assistant', content: '' })
      last = conv.messages[conv.messages.length - 1]
    }
    return last
  }

  function _assistantHasBody(m: Message | undefined): boolean {
    if (!m) return false
    return !!(
      (m.content || '').trim() ||
      m.attachments?.length ||
      m.charts?.length ||
      m.xhsCards?.length ||
      m.approval
    )
  }

  async function parseSSEStream(
    response: Response,
    convId: string,
    signal?: AbortSignal,
    sink?: Message,
  ) {
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    const onAbort = () => {
      void reader.cancel().catch(() => {})
    }
    if (signal) {
      if (signal.aborted) onAbort()
      else signal.addEventListener('abort', onAbort, { once: true })
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          buffer += decoder.decode() // flush decoder
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop()!

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          _handleSSELine(line, convId, sink)
        }
      }

      // process any remaining data in the buffer (e.g. last done event)
      const remaining = buffer.split('\n\n').filter(Boolean)
      for (const line of remaining) {
        if (!line.startsWith('data: ')) continue
        _handleSSELine(line, convId, sink)
      }
    } finally {
      if (signal) signal.removeEventListener('abort', onAbort)
    }
  }

  function _handleSSELine(line: string, convId: string, sink?: Message) {
    const payload: SSEPayload = JSON.parse(line.replace('data: ', ''))

    if (payload.type === 'status' && payload.content) {
      _patchRun(convId, { status: payload.content })
    } else if (payload.type === 'text' && payload.content) {
      // 正式 token 到来后收起思考态；已清空则勿反复 patch，避免 header 每 token 重渲
      if (runs.value[convId]?.status) {
        _patchRun(convId, { status: '' })
      }
      const conv = _getConv(convId)
      if (conv) {
        const safe = redactSecrets(payload.content)
        const last = _assistantSink(conv, sink)
        last.content += safe
        conv.updatedAt = Date.now()
      }
    } else if (payload.type === 'title' && payload.title) {
      const conv = _getConv(convId)
      if (conv) {
        conv.title = payload.title
      }
    } else if (payload.type === 'error' && payload.content) {
      const conv = _getConv(convId)
      const errText = toUserError(payload.content, '生成失败，请稍后重试。')
      if (conv) {
        const last = _assistantSink(conv, sink)
        last.content = last.content || errText
      }
    } else if (payload.type === 'usage') {
      const conv = _getConv(convId)
      if (conv) {
        const last = _assistantSink(conv, sink)
        const total = Number(payload.total_tokens) || 0
        if (total > 0) {
          last.usage = { total_tokens: total }
        }
      }
    } else if (payload.type === 'file' && payload.url) {
      const conv = _getConv(convId)
      if (conv) {
        const att = {
          url: payload.url,
          object_key: payload.object_key,
          mime_type: payload.mime_type,
          name: payload.name || 'download.docx',
        }
        const last = _assistantSink(conv, sink)
        if (!last.attachments) last.attachments = []
        last.attachments.push(att)
        conv.updatedAt = Date.now()
      }
    } else if (payload.type === 'chart' && payload.option) {
      const conv = _getConv(convId)
      if (conv) {
        const chart = {
          chart_id: String(payload.chart_id || ''),
          title: String(payload.title || ''),
          option: payload.option,
          evidence: payload.evidence,
        }
        const last = _assistantSink(conv, sink)
        if (!last.charts) last.charts = []
        last.charts.push(chart)
        conv.updatedAt = Date.now()
      }
    } else if (payload.type === 'xhs_card' && payload.index) {
      const conv = _getConv(convId)
      if (conv) {
        const rawErr = String(payload.error || '').trim()
        const card = {
          index: Number(payload.index) || 0,
          title: String(payload.title || ''),
          body: String(payload.body || ''),
          tags: Array.isArray(payload.tags) ? payload.tags.map(String) : [],
          image_url: String(payload.image_url || ''),
          error: rawErr ? toUserError(rawErr, '生成失败，请稍后重试。') : '',
        }
        const last = _assistantSink(conv, sink)
        if (!last.xhsCards) last.xhsCards = []
        const exist = last.xhsCards.findIndex((c) => c.index === card.index)
        if (exist >= 0) last.xhsCards[exist] = card
        else last.xhsCards.push(card)
        last.xhsCards.sort((a, b) => a.index - b.index)
        conv.updatedAt = Date.now()
      }
    } else if (payload.type === 'done') {
      const conv = _getConv(convId)
      if (conv && payload.title) {
        conv.title = payload.title
      }
      // 流结束仍无内容时给出明确提示（有附件/卡片则不强制文案）
      if (conv) {
        const last = sink || conv.messages[conv.messages.length - 1]
        if (
          last &&
          last.role === 'assistant' &&
          !last.content.trim() &&
          !(last.attachments && last.attachments.length) &&
          !(last.xhsCards && last.xhsCards.length) &&
          !(last.charts && last.charts.length) &&
          !last.approval
        ) {
          last.content = '未收到回复内容，请重试。'
        }
      }
    } else if (payload.type === 'interrupt') {
      const conv = _getConv(convId)
      if (conv) {
        const last = _assistantSink(conv, sink)
        const draft = payload.data?.draft
        last.approval = {
          status: 'pending',
          question: payload.data?.question || '需要您确认后才能继续。',
          action: payload.data?.action,
          draft: draft
            ? {
                to: String(draft.to || ''),
                subject: String(draft.subject || ''),
                body: String(draft.body || ''),
              }
            : undefined,
          editable: !!payload.data?.editable,
          fields: Array.isArray(payload.data?.fields)
            ? payload.data!.fields!.map(String)
            : undefined,
        }
        conv.updatedAt = Date.now()
      }
    }
  }

  async function uploadChatImage(file: File): Promise<MessageAttachment> {
    const formData = new FormData()
    formData.append('file', file)
    const res = await apiFetch('/api/chat/upload', {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      throw new Error(await errorFromResponse(res, '上传失败，请检查文件后重试。'))
    }
    const data: {
      url: string
      display_url: string
      object_key: string
      mime_type: string
      name: string
    } = await res.json()
    return {
      url: data.url,
      object_key: data.object_key,
      mime_type: data.mime_type,
      name: data.name,
      previewUrl: data.display_url || data.url,
    }
  }

  async function sendMessage(
    text: string,
    retryCount = 0,
    intent?: ClientIntent,
    attachments: MessageAttachment[] = [],
  ) {
    const trimmed = text.trim()
    const atts = attachments.filter((a) => a.url)
    if (!trimmed && atts.length === 0) return

    let conv = currentConversation.value
    if (!conv) conv = newConversation()

    const convId = conv.id
    // 仅锁定本会话；其它对话可并行发送；有待确认审批时禁止插话
    if (
      retryCount === 0 &&
      (isConversationBusy(convId) || !!_pendingApprovalMessage(conv))
    ) {
      return
    }

    // 重试时不重复追加用户气泡
    if (retryCount === 0) {
      conv.messages.push({
        role: 'user',
        content: trimmed,
        attachments: atts.length
          ? atts.map((a) => ({
              url: a.previewUrl || a.url,
              object_key: a.object_key,
              mime_type: a.mime_type,
              name: a.name,
            }))
          : undefined,
      })
      conv.updatedAt = Date.now()
      conv.messages.push({ role: 'assistant', content: '' })
    } else {
      const last = conv.messages[conv.messages.length - 1]
      if (last && last.role === 'assistant') last.content = ''
    }
    conv.updatedAt = Date.now()

    const hasAudio = atts.some((a) => {
      const mime = (a.mime_type || '').toLowerCase()
      const name = (a.name || a.url || '').toLowerCase()
      return mime.startsWith('audio/') || /\.mp3(\?|$)/i.test(name)
    })
    const hasImage = atts.some((a) => {
      const mime = (a.mime_type || '').toLowerCase()
      return mime.startsWith('image/')
    })
    let statusText = '正在思考…'
    if (intent === 'speech_recognize' || hasAudio) statusText = '正在识别…'
    else if (hasImage) statusText = '正在识别…'

    // 同会话重入：只 abort 本会话旧流，绝不打断其它对话
    abortByConv.get(convId)?.abort()
    const ac = new AbortController()
    abortByConv.set(convId, ac)
    stopRequestedByConv.set(convId, false)
    _patchRun(convId, {
      loading: true,
      streaming: true,
      status: statusText,
      approvalBusy: false,
    })

    try {
      const res = await apiFetch('/api/chat', {
        method: 'POST',
        signal: ac.signal,
        body: JSON.stringify({
          thread_id: conv.threadId,
          message: trimmed,
          intent: intent || undefined,
          attachments: atts.map((a) => ({
            url: a.url,
            object_key: a.object_key || '',
            mime_type: a.mime_type || '',
            name: a.name || '',
          })),
        }),
      })
      if (!res.ok || !res.body) {
        if (res.status === 503) {
          throw new Error('服务繁忙，请稍后重试。')
        }
        throw new Error(`HTTP ${res.status}`)
      }
      await parseSSEStream(res, convId, ac.signal)
      if (stopRequestedByConv.get(convId)) _applyStoppedReply(convId)
    } catch (err) {
      const aborted =
        !!stopRequestedByConv.get(convId) ||
        (err instanceof DOMException && err.name === 'AbortError') ||
        (err instanceof Error && err.name === 'AbortError')
      if (aborted) {
        _applyStoppedReply(convId)
      } else {
        console.error('请求失败', err)
        // 网络/连接失败自动重试一次（用户停止不重试）
        if (retryCount < 1) {
          console.warn('请求异常，自动重连一次')
          _patchRun(convId, { loading: false, streaming: false, status: '' })
          await new Promise((r) => setTimeout(r, 600))
          await sendMessage(text, retryCount + 1, intent, attachments)
          return
        }
        const live = _getConv(convId)
        const failMsg = live?.messages[live.messages.length - 1]
        if (failMsg && failMsg.role === 'assistant' && failMsg.content === '') {
          failMsg.content =
            err instanceof Error && err.message.includes('繁忙')
              ? err.message
              : '请求失败，请检查后端服务是否启动。'
          failMsg.role = 'system' as Message['role']
        }
      }
    } finally {
      // 若已被 approve/stop 替换 AbortController，勿清掉后来者的 streaming
      if (abortByConv.get(convId) === ac) {
        abortByConv.delete(convId)
        stopRequestedByConv.delete(convId)
        _patchRun(convId, {
          loading: false,
          streaming: false,
          status: '',
        })
      }
    }
  }

  /** 停止当前回答：先通知后端取消任务，再 abort 本会话 SSE（不影响其它对话） */
  async function stopGeneration() {
    const conv = currentConversation.value
    const convId = conv?.id || currentId.value
    if (!convId || !isConversationStreaming(convId)) return

    stopRequestedByConv.set(convId, true)
    if (conv?.threadId) {
      void apiFetch('/api/chat/stop', {
        method: 'POST',
        body: JSON.stringify({ thread_id: conv.threadId }),
      }).catch(() => {})
    }
    abortByConv.get(convId)?.abort()
    _applyStoppedReply(convId)
    _patchRun(convId, { loading: false, streaming: false, status: '' })
  }

  function _pendingApprovalMessage(conv: Conversation): Message | null {
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      const m = conv.messages[i]
      if (m.role === 'assistant' && m.approval?.status === 'pending') return m
    }
    return null
  }

  async function handleUserChoice(
    isApproved: boolean,
    editedArgs?: Record<string, string>,
  ) {
    const conv = currentConversation.value
    if (!conv) return
    const convId = conv.id
    if (_ensureRun(convId).approvalBusy || isConversationStreaming(convId)) return

    const target = _pendingApprovalMessage(conv)
    if (!target) return

    target.approval = undefined
    _patchRun(convId, {
      approvalBusy: true,
      loading: true,
      streaming: true,
      status: isApproved ? '正在继续…' : '正在取消…',
    })

    abortByConv.get(convId)?.abort()
    const ac = new AbortController()
    abortByConv.set(convId, ac)
    stopRequestedByConv.set(convId, false)

    try {
      const body: Record<string, unknown> = {
        thread_id: conv.threadId,
        approved: isApproved,
        reason: '',
      }
      if (isApproved && editedArgs && Object.keys(editedArgs).length) {
        body.edited_args = editedArgs
      }
      const res = await apiFetch('/api/approve', {
        method: 'POST',
        signal: ac.signal,
        body: JSON.stringify(body),
      })
      await parseSSEStream(res, convId, ac.signal, target)
      if (stopRequestedByConv.get(convId)) {
        if (!_assistantHasBody(target)) target.content = STOPPED_REPLY
      } else if (!_assistantHasBody(target)) {
        target.content = isApproved ? '已处理完成' : '任务已取消'
      }
    } catch (err) {
      const aborted =
        !!stopRequestedByConv.get(convId) ||
        (err instanceof DOMException && err.name === 'AbortError') ||
        (err instanceof Error && err.name === 'AbortError')
      if (aborted) {
        if (!_assistantHasBody(target)) target.content = STOPPED_REPLY
      } else {
        console.error('恢复执行失败', err)
        if (!_assistantHasBody(target)) {
          target.content = isApproved ? '继续执行失败，请重试。' : '任务已取消'
        }
      }
    } finally {
      if (abortByConv.get(convId) === ac) {
        abortByConv.delete(convId)
        stopRequestedByConv.delete(convId)
        _patchRun(convId, {
          approvalBusy: false,
          loading: false,
          streaming: false,
          status: '',
        })
      } else {
        _patchRun(convId, { approvalBusy: false })
      }
    }
  }

  return {
    activeView,
    conversations,
    conversationsLoadError,
    currentId,
    runs,
    loading,
    streaming,
    streamingStatus,
    initialLoading,
    initialLoaded,
    isMobile,
    sidebarOpen,
    toggleSidebar,
    openSidebar,
    closeSidebar,
    closeSidebarIfMobile,
    initSidebarLayout,
    teardownSidebarLayout,
    approvalBusy,
    isConversationBusy,
    isConversationStreaming,
    conversationStatus,
    currentConversation,
    sortedConversations,
    resetLocalState,
    loadConversations,
    loadMessages,
    restoreCurrentId,
    newConversation,
    selectConversation,
    deleteConversation,
    deleteAllConversations,
    clearConversation,
    uploadChatImage,
    sendMessage,
    stopGeneration,
    handleUserChoice,
  }
})
