import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { apiFetch } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import { errorFromResponse, toUserError } from '@/utils/safeError'

export const MOSS_MODEL = 'fnlp/MOSS-TTSD-v0.5'
export const COSY_MODEL = 'FunAudioLLM/CosyVoice2-0.5B'

export type VoiceCloneModel = typeof MOSS_MODEL | typeof COSY_MODEL
export type VoiceClonePhase =
  | 'idle'
  | 'uploading'
  | 'transcribing'
  | 'synthesizing'
  | 'loadingHistory'

export interface VoiceCloneHistoryItem {
  id: number
  audioUrl: string
  speakText: string
  model: VoiceCloneModel | string
  speed: number
  refFileName: string
  createdAt: number
  userId: number
}

interface HistoryApiItem {
  id: number
  user_id: number
  speak_text?: string
  model?: string
  speed?: number
  ref_file_name?: string
  audio_url?: string
  display_url?: string
  created_at?: string
}

function mapHistoryItem(raw: HistoryApiItem): VoiceCloneHistoryItem {
  const created = raw.created_at ? Date.parse(raw.created_at) : Date.now()
  return {
    id: raw.id,
    audioUrl: (raw.display_url || raw.audio_url || '').trim(),
    speakText: (raw.speak_text || '').trim(),
    model: (raw.model || MOSS_MODEL) as VoiceCloneModel,
    speed: Number(raw.speed ?? 1) || 1,
    refFileName: (raw.ref_file_name || '参考音').trim() || '参考音',
    createdAt: Number.isFinite(created) ? created : Date.now(),
    userId: raw.user_id,
  }
}

export const useVoiceCloneStore = defineStore('voiceClone', () => {
  const ownerUserId = ref<number | null>(null)
  const audioUrl = ref('')
  const displayUrl = ref('')
  const originalAudioUrl = ref('')
  const fileName = ref('')
  const referenceText = ref('')
  const speakText = ref('')
  const model = ref<VoiceCloneModel>(MOSS_MODEL)
  const speed = ref(1)
  const truncated = ref(false)
  const phase = ref<VoiceClonePhase>('idle')
  const error = ref('')
  const history = ref<VoiceCloneHistoryItem[]>([])
  const activeHistoryId = ref<number | null>(null)
  const historyLoading = ref(false)

  const busy = computed(() => phase.value !== 'idle')
  const canSynthesize = computed(
    () =>
      !!audioUrl.value &&
      !!referenceText.value.trim() &&
      !!speakText.value.trim() &&
      !busy.value,
  )
  const activeResult = computed(
    () => history.value.find((h) => h.id === activeHistoryId.value) || history.value[0] || null,
  )

  function resetReference() {
    audioUrl.value = ''
    displayUrl.value = ''
    originalAudioUrl.value = ''
    fileName.value = ''
    referenceText.value = ''
    truncated.value = false
    error.value = ''
  }

  function clearLocalHistory() {
    history.value = []
    activeHistoryId.value = null
  }

  /** 登出 / 切用户：清空本地工作台与历史缓存（不删服务端） */
  function resetAll() {
    resetReference()
    speakText.value = ''
    model.value = MOSS_MODEL
    speed.value = 1
    phase.value = 'idle'
    historyLoading.value = false
    clearLocalHistory()
    ownerUserId.value = null
  }

  async function loadHistory() {
    const uid = ownerUserId.value
    if (uid == null) return
    historyLoading.value = true
    try {
      const res = await apiFetch('/api/voice-clone/history')
      if (!res.ok) {
        throw new Error(await errorFromResponse(res, '加载合成历史失败'))
      }
      const data = (await res.json()) as { items?: HistoryApiItem[] }
      if (ownerUserId.value !== uid) return
      history.value = (data.items || []).map(mapHistoryItem)
      activeHistoryId.value = history.value[0]?.id ?? null
    } catch (e) {
      if (ownerUserId.value === uid) {
        error.value = toUserError(
          e instanceof Error ? e.message : String(e),
          '加载合成历史失败',
        )
      }
    } finally {
      historyLoading.value = false
    }
  }

  /**
   * 绑定当前登录用户；切用户时清空本地并拉取该用户历史。
   */
  async function ensureUserScope(): Promise<boolean> {
    const auth = useAuthStore()
    const uid = auth.user?.id ?? null
    if (uid == null) {
      if (ownerUserId.value != null || history.value.length || audioUrl.value) {
        resetAll()
      }
      return false
    }
    if (ownerUserId.value !== uid) {
      resetReference()
      speakText.value = ''
      model.value = MOSS_MODEL
      speed.value = 1
      phase.value = 'idle'
      clearLocalHistory()
      ownerUserId.value = uid
      await loadHistory()
    }
    return true
  }

  async function removeHistory(id: number) {
    if (!(await ensureUserScope())) return
    const res = await apiFetch(`/api/voice-clone/history/${id}`, { method: 'DELETE' })
    if (!res.ok) {
      error.value = await errorFromResponse(res, '删除失败')
      return
    }
    history.value = history.value.filter((h) => h.id !== id)
    if (activeHistoryId.value === id) {
      activeHistoryId.value = history.value[0]?.id ?? null
    }
  }

  async function clearHistory() {
    if (!(await ensureUserScope())) return
    const res = await apiFetch('/api/voice-clone/history', { method: 'DELETE' })
    if (!res.ok) {
      error.value = await errorFromResponse(res, '清空失败')
      return
    }
    clearLocalHistory()
  }

  function selectHistory(id: number) {
    if (history.value.some((h) => h.id === id && h.userId === ownerUserId.value)) {
      activeHistoryId.value = id
    }
  }

  function clampSpeed(v: number): number {
    if (!Number.isFinite(v)) return 1
    return Math.min(4, Math.max(0.25, Math.round(v * 100) / 100))
  }

  function modelLabel(m: string): string {
    return m === COSY_MODEL ? 'CosyVoice' : 'MOSS'
  }

  async function runTranscribe(sourceUrl: string) {
    const trRes = await apiFetch('/api/voice-clone/transcribe', {
      method: 'POST',
      body: JSON.stringify({ audio_url: sourceUrl }),
    })
    if (!trRes.ok) {
      throw new Error(await errorFromResponse(trRes, '参考音转写失败'))
    }
    const tr = (await trRes.json()) as {
      text?: string
      audio_url?: string
      truncated?: boolean
    }
    const asrUrl = (tr.audio_url || sourceUrl).trim()
    if (asrUrl) audioUrl.value = asrUrl
    truncated.value = !!tr.truncated
    referenceText.value = (tr.text || '').trim()
    if (!referenceText.value) {
      throw new Error('转写结果为空，请手动填写参考音原文')
    }
  }

  async function uploadAndTranscribe(file: File) {
    if (!(await ensureUserScope()) || busy.value) return
    resetReference()
    phase.value = 'uploading'
    error.value = ''

    try {
      const form = new FormData()
      form.append('file', file)
      const uploadRes = await apiFetch('/api/voice-clone/upload', {
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

      originalAudioUrl.value = url
      audioUrl.value = url
      displayUrl.value = (uploaded.display_url || url).trim()
      fileName.value = uploaded.name || file.name

      phase.value = 'transcribing'
      await runTranscribe(url)
    } catch (e) {
      error.value = toUserError(
        e instanceof Error ? e.message : String(e),
        '上传或转写失败，请稍后重试',
      )
    } finally {
      phase.value = 'idle'
    }
  }

  async function retranscribe() {
    if (!(await ensureUserScope())) return
    const source = originalAudioUrl.value || audioUrl.value
    if (!source || busy.value) return
    phase.value = 'transcribing'
    error.value = ''
    try {
      await runTranscribe(source)
    } catch (e) {
      error.value = toUserError(
        e instanceof Error ? e.message : String(e),
        '转写失败，请稍后重试',
      )
    } finally {
      phase.value = 'idle'
    }
  }

  async function synthesize() {
    if (!(await ensureUserScope()) || !canSynthesize.value) return
    const uid = ownerUserId.value
    if (uid == null) return
    phase.value = 'synthesizing'
    error.value = ''
    try {
      const usedSpeed = clampSpeed(speed.value)
      const usedModel = model.value
      const usedText = speakText.value.trim()
      const res = await apiFetch('/api/voice-clone/synthesize', {
        method: 'POST',
        body: JSON.stringify({
          audio_url: audioUrl.value,
          reference_text: referenceText.value.trim(),
          input: usedText,
          model: usedModel,
          speed: usedSpeed,
          ref_file_name: fileName.value || '',
        }),
      })
      if (!res.ok) {
        throw new Error(await errorFromResponse(res, '语音合成失败'))
      }
      const raw = (await res.json()) as HistoryApiItem
      if (ownerUserId.value !== uid) return
      const item = mapHistoryItem(raw)
      history.value = [item, ...history.value.filter((h) => h.id !== item.id)]
      activeHistoryId.value = item.id
    } catch (e) {
      error.value = toUserError(
        e instanceof Error ? e.message : String(e),
        '语音合成失败，请稍后重试',
      )
    } finally {
      phase.value = 'idle'
    }
  }

  return {
    ownerUserId,
    audioUrl,
    displayUrl,
    originalAudioUrl,
    fileName,
    referenceText,
    speakText,
    model,
    speed,
    truncated,
    phase,
    error,
    history,
    activeHistoryId,
    historyLoading,
    activeResult,
    busy,
    canSynthesize,
    clampSpeed,
    modelLabel,
    ensureUserScope,
    loadHistory,
    resetReference,
    resetAll,
    clearHistory,
    removeHistory,
    selectHistory,
    uploadAndTranscribe,
    retranscribe,
    synthesize,
  }
})
