import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { apiFetch } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { errorFromResponse, toUserError } from '@/utils/safeError'

export type TranscribePhase = 'idle' | 'uploading' | 'transcribing'

interface TranscribeSsePayload {
  type?: string
  phase?: string
  message?: string
  detail?: string
  percent?: number
  current?: number
  total?: number
  text?: string
  segment_text?: string
  duration_sec?: number | null
}

export const useTranscribeStore = defineStore('transcribe', () => {
  const ownerUserId = ref<number | null>(null)
  const audioUrl = ref('')
  const displayUrl = ref('')
  const fileName = ref('')
  const text = ref('')
  const phase = ref<TranscribePhase>('idle')
  const error = ref('')
  const copied = ref(false)

  const progressPercent = ref(0)
  const progressCurrent = ref(0)
  const progressTotal = ref(0)
  const progressMessage = ref('')
  const durationSec = ref<number | null>(null)

  let abortController: AbortController | null = null

  const busy = computed(() => phase.value !== 'idle')
  const hasResult = computed(() => !!text.value.trim())
  const canStart = computed(() => !!audioUrl.value && !busy.value)
  const showProgress = computed(
    () => phase.value === 'transcribing' || (phase.value === 'idle' && progressPercent.value > 0 && progressPercent.value < 100),
  )

  function resetProgress() {
    progressPercent.value = 0
    progressCurrent.value = 0
    progressTotal.value = 0
    progressMessage.value = ''
    durationSec.value = null
  }

  function resetResult() {
    audioUrl.value = ''
    displayUrl.value = ''
    fileName.value = ''
    text.value = ''
    error.value = ''
    copied.value = false
    resetProgress()
  }

  /** 登出 / 切用户：清空本地工作台 */
  function resetAll() {
    abortController?.abort()
    abortController = null
    resetResult()
    phase.value = 'idle'
    ownerUserId.value = null
  }

  async function ensureUserScope(): Promise<boolean> {
    const auth = useAuthStore()
    const uid = auth.user?.id ?? null
    if (uid == null) {
      if (ownerUserId.value != null || audioUrl.value || text.value) {
        resetAll()
      }
      return false
    }
    if (ownerUserId.value !== uid) {
      abortController?.abort()
      abortController = null
      resetResult()
      phase.value = 'idle'
      ownerUserId.value = uid
    }
    return true
  }

  function applySse(payload: TranscribeSsePayload) {
    if (typeof payload.percent === 'number' && Number.isFinite(payload.percent)) {
      progressPercent.value = Math.max(0, Math.min(100, Math.round(payload.percent)))
    }
    if (typeof payload.current === 'number') progressCurrent.value = payload.current
    if (typeof payload.total === 'number') progressTotal.value = payload.total
    if (typeof payload.duration_sec === 'number' && Number.isFinite(payload.duration_sec)) {
      durationSec.value = payload.duration_sec
    }
    if (payload.message) progressMessage.value = payload.message

    if (payload.type === 'progress' || payload.type === 'done') {
      if (typeof payload.text === 'string') {
        text.value = payload.text
      }
    }
    if (payload.type === 'error' && typeof payload.text === 'string' && payload.text.trim()) {
      // 中途失败时保留已识别部分
      text.value = payload.text
    }
  }

  async function parseTranscribeSse(response: Response, signal: AbortSignal) {
    const reader = response.body?.getReader()
    if (!reader) throw new Error('浏览器不支持流式读取')
    const decoder = new TextDecoder()
    let buffer = ''
    let sawDone = false
    let lastError = ''

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
          let payload: TranscribeSsePayload
          try {
            payload = JSON.parse(line.slice(6)) as TranscribeSsePayload
          } catch {
            continue
          }
          applySse(payload)
          if (payload.type === 'done') {
            sawDone = true
            if (typeof payload.text === 'string') text.value = payload.text.trim()
            progressPercent.value = 100
            progressMessage.value = payload.message || '转写完成'
          }
          if (payload.type === 'error') {
            lastError = (payload.detail || payload.message || '音频转写失败').trim()
          }
        }
      }

      const remaining = buffer.split('\n\n').filter(Boolean)
      for (const chunk of remaining) {
        const line = chunk
          .split('\n')
          .map((l) => l.trimEnd())
          .find((l) => l.startsWith('data: '))
        if (!line) continue
        try {
          const payload = JSON.parse(line.slice(6)) as TranscribeSsePayload
          applySse(payload)
          if (payload.type === 'done') {
            sawDone = true
            if (typeof payload.text === 'string') text.value = payload.text.trim()
            progressPercent.value = 100
          }
          if (payload.type === 'error') {
            lastError = (payload.detail || payload.message || '音频转写失败').trim()
          }
        } catch {
          /* ignore */
        }
      }
    } finally {
      signal.removeEventListener('abort', onAbort)
    }

    if (signal.aborted) {
      throw new Error('已取消转写')
    }
    if (lastError && !sawDone) {
      throw new Error(lastError)
    }
    if (!sawDone && !text.value.trim()) {
      throw new Error('转写未完成，请稍后重试')
    }
    if (!text.value.trim()) {
      throw new Error('转写结果为空，请换一段音频重试')
    }
  }

  async function runTranscribe(sourceUrl: string) {
    abortController?.abort()
    const ac = new AbortController()
    abortController = ac
    resetProgress()
    progressMessage.value = '正在分析音频…'
    progressPercent.value = 2

    const trRes = await apiFetch(
      '/api/transcribe',
      {
        method: 'POST',
        body: JSON.stringify({ audio_url: sourceUrl }),
        signal: ac.signal,
      },
      { retry: false },
    )
    if (!trRes.ok) {
      throw new Error(await errorFromResponse(trRes, '音频转写失败'))
    }
    const ctype = (trRes.headers.get('content-type') || '').toLowerCase()
    if (!ctype.includes('text/event-stream')) {
      // 兼容旧 JSON 响应
      const tr = (await trRes.json()) as { text?: string }
      text.value = (tr.text || '').trim()
      progressPercent.value = 100
      progressMessage.value = '转写完成'
      if (!text.value) throw new Error('转写结果为空，请换一段音频重试')
      return
    }
    await parseTranscribeSse(trRes, ac.signal)
  }

  /** 仅上传，不自动转写 */
  async function upload(file: File) {
    if (!(await ensureUserScope()) || busy.value) return
    resetResult()
    phase.value = 'uploading'
    error.value = ''

    try {
      const form = new FormData()
      form.append('file', file)
      const uploadRes = await apiFetch('/api/transcribe/upload', {
        method: 'POST',
        body: form,
      })
      if (!uploadRes.ok) {
        throw new Error(await errorFromResponse(uploadRes, '上传失败'))
      }
      const uploaded = (await uploadRes.json()) as {
        url?: string
        display_url?: string
        name?: string
      }
      const url = (uploaded.url || '').trim()
      if (!url) throw new Error('上传成功但未返回音频地址')

      audioUrl.value = url
      displayUrl.value = (uploaded.display_url || url).trim()
      fileName.value = uploaded.name || file.name
    } catch (e) {
      error.value = toUserError(
        e instanceof Error ? e.message : String(e),
        '上传失败，请稍后重试',
      )
    } finally {
      phase.value = 'idle'
    }
  }

  /** 用户点击后开始转写 */
  async function startTranscribe() {
    if (!(await ensureUserScope())) return
    const source = audioUrl.value
    if (!source || busy.value) return
    phase.value = 'transcribing'
    error.value = ''
    copied.value = false
    text.value = ''
    resetProgress()
    try {
      await runTranscribe(source)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        error.value = '已取消转写'
      } else {
        error.value = toUserError(
          e instanceof Error ? e.message : String(e),
          '转写失败，请稍后重试',
        )
      }
    } finally {
      phase.value = 'idle'
      if (abortController) abortController = null
    }
  }

  async function retranscribe() {
    await startTranscribe()
  }

  function cancel() {
    if (phase.value !== 'transcribing') return
    abortController?.abort()
  }

  async function copyText(): Promise<boolean> {
    const value = text.value
    if (!value) return false
    try {
      await navigator.clipboard.writeText(value)
      copied.value = true
      window.setTimeout(() => {
        copied.value = false
      }, 2000)
      return true
    } catch {
      error.value = '复制失败，请手动选择文本'
      return false
    }
  }

  function downloadText() {
    const value = text.value
    if (!value) return
    const stamp = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    const base =
      (fileName.value || 'transcript').replace(/\.[^.]+$/, '') || 'transcript'
    const name = `${base}_${stamp.getFullYear()}${pad(stamp.getMonth() + 1)}${pad(stamp.getDate())}_${pad(stamp.getHours())}${pad(stamp.getMinutes())}.txt`
    const blob = new Blob([value], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  }

  return {
    ownerUserId,
    audioUrl,
    displayUrl,
    fileName,
    text,
    phase,
    error,
    copied,
    progressPercent,
    progressCurrent,
    progressTotal,
    progressMessage,
    durationSec,
    busy,
    hasResult,
    canStart,
    showProgress,
    ensureUserScope,
    resetResult,
    resetAll,
    upload,
    startTranscribe,
    retranscribe,
    cancel,
    copyText,
    downloadText,
  }
})
