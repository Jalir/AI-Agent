/**
 * 阿里云 NLS 实时语音识别（浏览器直连 WebSocket）。
 * Token / AppKey 由后端 /api/speech/token 下发。
 */

import { apiFetch } from '@/api/http'

const TARGET_SAMPLE_RATE = 16000

export type RealtimeAsrStatus = 'idle' | 'connecting' | 'listening' | 'stopping'

export interface RealtimeAsrHandlers {
  onPartial?: (text: string) => void
  /** 一句结束（静音断句）；返回 true 表示由调用方接管收尾（如自动发送） */
  onSentenceEnd?: (text: string) => boolean | void
  onStatus?: (status: RealtimeAsrStatus) => void
  onError?: (message: string) => void
}

interface TokenPayload {
  token: string
  app_key: string
  gateway_url: string
  expire_time: number
}

function uuid32(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

function downsampleToPcm16(input: Float32Array, fromRate: number, toRate: number): Int16Array {
  if (fromRate === toRate) {
    const out = new Int16Array(input.length)
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]!))
      out[i] = s < 0 ? (s * 0x8000) | 0 : (s * 0x7fff) | 0
    }
    return out
  }
  const ratio = fromRate / toRate
  const newLen = Math.max(1, Math.floor(input.length / ratio))
  const out = new Int16Array(newLen)
  for (let i = 0; i < newLen; i++) {
    const start = Math.floor(i * ratio)
    const end = Math.min(Math.floor((i + 1) * ratio), input.length)
    let sum = 0
    let count = 0
    for (let j = start; j < end; j++) {
      sum += input[j]!
      count++
    }
    const s = Math.max(-1, Math.min(1, count ? sum / count : 0))
    out[i] = s < 0 ? (s * 0x8000) | 0 : (s * 0x7fff) | 0
  }
  return out
}

async function fetchToken(): Promise<TokenPayload> {
  const res = await apiFetch('/api/speech/token')
  if (!res.ok) {
    let detail = '获取语音 Token 失败'
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return (await res.json()) as TokenPayload
}

function micDiagnostics(): string {
  const host = typeof location !== 'undefined' ? location.host : '?'
  const proto = typeof location !== 'undefined' ? location.protocol : '?'
  const secure =
    typeof window !== 'undefined' ? String(window.isSecureContext) : '?'
  return `页面=${proto}//${host} secure=${secure}`
}

function micErrorMessage(err: unknown): string {
  const name = err instanceof DOMException ? err.name : ''
  const raw = err instanceof Error ? err.message : String(err || '')
  const diag = micDiagnostics()

  if (typeof window !== 'undefined' && !window.isSecureContext) {
    return (
      `当前页面不是安全上下文，浏览器会拒绝麦克风。请用 http://localhost:端口 或 https 打开（不要用局域网 IP）。\n(${diag})`
    )
  }
  if (name === 'NotFoundError' || /Requested device not found/i.test(raw)) {
    return `未检测到麦克风设备。请检查系统声音输入设备是否可用。\n(${diag})`
  }
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return (
      '麦克风被拒绝（NotAllowedError）。常见原因：\n' +
      '1) 浏览器站点权限仍是「禁止」：地址栏锁图标 → 麦克风 → 允许，或清除该站点权限后刷新\n' +
      '2) Windows：设置 → 隐私和安全性 → 麦克风 → 打开「允许桌面应用访问麦克风」\n' +
      '3) 请用系统 Chrome/Edge 打开，不要用 IDE 内置预览/Simple Browser\n' +
      '4) 关闭其它占用麦克风的软件后重试\n' +
      `(${diag}; ${name || 'Error'}: ${raw})`
    )
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return `麦克风被占用或无法打开，请关闭其它录音软件后重试。\n(${diag}; ${name}: ${raw})`
  }
  if (name === 'SecurityError') {
    return `安全策略阻止麦克风，请用 localhost 或 HTTPS 访问。\n(${diag})`
  }
  return `${raw || '无法打开麦克风'}\n(${diag}; ${name || 'Error'})`
}

async function openMicrophone(): Promise<MediaStream> {
  if (typeof window !== 'undefined' && !window.isSecureContext) {
    throw new DOMException(
      'Microphone requires a secure context (localhost or HTTPS)',
      'SecurityError',
    )
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('当前浏览器不支持麦克风采集（mediaDevices 不可用）')
  }

  try {
    // 仅 audio:true，避免约束过严；必须在用户点击手势内尽快调用
    return await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (err) {
    console.error('[realtimeAsr] getUserMedia failed', err, micDiagnostics())
    throw err instanceof Error ? err : new Error(micErrorMessage(err))
  }
}

export class RealtimeAsrSession {
  private handlers: RealtimeAsrHandlers
  private ws: WebSocket | null = null
  private mediaStream: MediaStream | null = null
  private audioContext: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private taskId = ''
  private appKey = ''
  private started = false
  private ready = false
  private closed = false
  private finalizedText = ''
  private partialText = ''
  private status: RealtimeAsrStatus = 'idle'

  constructor(handlers: RealtimeAsrHandlers = {}) {
    this.handlers = handlers
  }

  get isActive(): boolean {
    return this.status === 'connecting' || this.status === 'listening' || this.status === 'stopping'
  }

  private setStatus(s: RealtimeAsrStatus) {
    this.status = s
    this.handlers.onStatus?.(s)
  }

  private emitText() {
    const joined = [this.finalizedText, this.partialText].filter(Boolean).join('')
    this.handlers.onPartial?.(joined)
  }

  async start(): Promise<void> {
    if (this.isActive) return
    this.closed = false
    this.started = false
    this.ready = false
    this.finalizedText = ''
    this.partialText = ''
    this.setStatus('connecting')

    try {
      // 先开麦（保留用户点击手势），再取 Token，避免权限弹窗被异步打乱
      this.mediaStream = await openMicrophone()

      const creds = await fetchToken()
      this.appKey = creds.app_key
      this.taskId = uuid32()

      const url = `${creds.gateway_url}?token=${encodeURIComponent(creds.token)}`
      await new Promise<void>((resolve, reject) => {
        const ws = new WebSocket(url)
        this.ws = ws
        ws.binaryType = 'arraybuffer'

        const fail = (msg: string) => {
          reject(new Error(msg))
        }

        ws.onopen = () => {
          this.sendStart()
        }

        ws.onmessage = (ev) => {
          if (typeof ev.data !== 'string') return
          try {
            const msg = JSON.parse(ev.data) as {
              header?: { name?: string; status?: number; status_message?: string }
              payload?: { result?: string }
            }
            const name = msg.header?.name || ''
            const status = msg.header?.status
            if (status != null && status !== 20000000) {
              const errMsg = msg.header?.status_message || `识别错误 ${status}`
              if (!this.started) {
                fail(errMsg)
                return
              }
              this.handlers.onError?.(errMsg)
              void this.stop({ send: false })
              return
            }

            if (name === 'TranscriptionStarted') {
              this.ready = true
              this.started = true
              this.setStatus('listening')
              this.beginAudioPump()
              resolve()
              return
            }

            if (name === 'TranscriptionResultChanged') {
              this.partialText = String(msg.payload?.result || '')
              this.emitText()
              return
            }

            if (name === 'SentenceEnd') {
              const sentence = String(msg.payload?.result || '').trim()
              if (sentence) {
                this.finalizedText += sentence
                this.partialText = ''
                this.emitText()
                const full = this.finalizedText.trim()
                // 返回 true：停说后由调用方接管（如自动发送）
                if (this.handlers.onSentenceEnd?.(full)) {
                  this.ready = false
                  if (this.status === 'listening') {
                    void this.stop({ send: true })
                  }
                }
              }
              return
            }

            if (name === 'TranscriptionCompleted') {
              void this.cleanupMedia()
              this.setStatus('idle')
            }
          } catch {
            /* ignore parse errors */
          }
        }

        ws.onerror = () => {
          if (!this.started) fail('语音服务连接失败')
          else this.handlers.onError?.('语音连接异常')
        }

        ws.onclose = () => {
          if (!this.started && !this.closed) fail('语音连接已关闭')
        }
      })
    } catch (err) {
      await this.cleanupAll()
      this.setStatus('idle')
      const message = micErrorMessage(err)
      this.handlers.onError?.(message)
      throw new Error(message)
    }
  }

  private sendStart() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
    this.ws.send(
      JSON.stringify({
        header: {
          message_id: uuid32(),
          task_id: this.taskId,
          namespace: 'SpeechTranscriber',
          name: 'StartTranscription',
          appkey: this.appKey,
        },
        payload: {
          format: 'pcm',
          sample_rate: TARGET_SAMPLE_RATE,
          enable_intermediate_result: true,
          enable_punctuation_prediction: true,
          enable_inverse_text_normalization: true,
          // 静音断句：约 1s 无语音视为一句结束
          max_sentence_silence: 1000,
        },
      }),
    )
  }

  private beginAudioPump() {
    if (!this.mediaStream) return
    const ctx = new AudioContext()
    this.audioContext = ctx
    void ctx.resume()
    const source = ctx.createMediaStreamSource(this.mediaStream)
    this.source = source
    // 4096：兼容性较好；按实时速率推送
    const processor = ctx.createScriptProcessor(4096, 1, 1)
    this.processor = processor
    processor.onaudioprocess = (e) => {
      if (!this.ready || !this.ws || this.ws.readyState !== WebSocket.OPEN || this.closed) return
      const input = e.inputBuffer.getChannelData(0)
      const pcm = downsampleToPcm16(input, ctx.sampleRate, TARGET_SAMPLE_RATE)
      this.ws.send(pcm.buffer)
    }
    // 静音接到 destination，避免扬声器回放麦克风
    const mute = ctx.createGain()
    mute.gain.value = 0
    source.connect(processor)
    processor.connect(mute)
    mute.connect(ctx.destination)
  }

  private sendStop() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
    try {
      this.ws.send(
        JSON.stringify({
          header: {
            message_id: uuid32(),
            task_id: this.taskId,
            namespace: 'SpeechTranscriber',
            name: 'StopTranscription',
            appkey: this.appKey,
          },
        }),
      )
    } catch {
      /* ignore */
    }
  }

  private async cleanupMedia() {
    try {
      this.processor?.disconnect()
      this.source?.disconnect()
    } catch {
      /* ignore */
    }
    this.processor = null
    this.source = null
    if (this.audioContext) {
      try {
        await this.audioContext.close()
      } catch {
        /* ignore */
      }
      this.audioContext = null
    }
    if (this.mediaStream) {
      for (const t of this.mediaStream.getTracks()) t.stop()
      this.mediaStream = null
    }
    this.ready = false
  }

  private async cleanupAll() {
    this.closed = true
    this.ready = false
    await this.cleanupMedia()
    if (this.ws) {
      try {
        this.ws.onopen = null
        this.ws.onmessage = null
        this.ws.onerror = null
        this.ws.onclose = null
        if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
          this.ws.close()
        }
      } catch {
        /* ignore */
      }
      this.ws = null
    }
  }

  /** 停止识别；send=false 时仅取消 */
  async stop(opts: { send?: boolean } = {}): Promise<string> {
    if (this.status === 'idle') {
      return [this.finalizedText, this.partialText].filter(Boolean).join('').trim()
    }
    this.setStatus('stopping')
    this.ready = false
    await this.cleanupMedia()
    if (opts.send !== false) this.sendStop()
    // 稍等 TranscriptionCompleted；超时也清理
    await new Promise<void>((resolve) => {
      const ws = this.ws
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        resolve()
        return
      }
      const timer = window.setTimeout(() => resolve(), 800)
      const prev = ws.onmessage
      ws.onmessage = (ev) => {
        prev?.call(ws, ev)
        if (typeof ev.data === 'string' && ev.data.includes('TranscriptionCompleted')) {
          window.clearTimeout(timer)
          resolve()
        }
      }
    })
    const text = [this.finalizedText, this.partialText].filter(Boolean).join('').trim()
    await this.cleanupAll()
    this.setStatus('idle')
    return text
  }
}
