/** 前端展示用：绝不把密钥、签名 URL、堆栈等后端秘密显示给用户。 */

const SENSITIVE_RE =
  /https?:\/\/\S+|OSSAccessKeyId\s*=|Signature\s*=|Expires\s*=|AccessKey(?:Id|Secret)\s*[=:]|sk-[A-Za-z0-9]{8,}|(?:api[_-]?key|secret|token|password)\s*[:=]\s*\S+|Bearer\s+\S+|traceback\s*\(most recent call last\)|File\s+"[^"]+\.py"|[A-Za-z]:\\[^\s]+/i

const REDACT_RULES: { re: RegExp; to: string }[] = [
  {
    re: /https?:\/\/[^\s\]\)'"]+(?:OSSAccessKeyId|Signature|Expires)=[^\s\]\)'"]+/gi,
    to: '[签名链接已隐藏]',
  },
  { re: /OSSAccessKeyId=[^&\s"']+/gi, to: 'OSSAccessKeyId=[已隐藏]' },
  { re: /Signature=[^&\s"']+/gi, to: 'Signature=[已隐藏]' },
  { re: /\bsk-[A-Za-z0-9]{8,}\b/g, to: 'sk-[已隐藏]' },
  { re: /Bearer\s+\S+/gi, to: 'Bearer [已隐藏]' },
]

const DEFAULT_ERROR = '操作失败，请稍后重试。'

export function looksSensitive(text: string | null | undefined): boolean {
  const raw = String(text || '').trim()
  if (!raw) return false
  return SENSITIVE_RE.test(raw)
}

/** 就地打码；用于气泡正文等。 */
export function redactSecrets(text: string | null | undefined): string {
  let s = String(text ?? '')
  for (const { re, to } of REDACT_RULES) {
    s = s.replace(re, to)
  }
  return s
}

/**
 * 用户可见错误文案。
 * - 含敏感信息 / 过长 / 像堆栈 → 固定短句
 * - 否则打码后返回
 */
export function toUserError(
  text: string | null | undefined,
  fallback: string = DEFAULT_ERROR,
): string {
  const raw = String(text || '').trim()
  if (!raw) return fallback
  if (raw.length > 200 || looksSensitive(raw)) return fallback
  const low = raw.toLowerCase()
  if (
    low.includes('traceback') ||
    low.includes('file "') ||
    low.includes('.py') ||
    low.includes('status code') ||
    low.includes('request id')
  ) {
    return fallback
  }
  return redactSecrets(raw)
}

/** 从 fetch 失败响应取 detail，始终过 toUserError。 */
export async function errorFromResponse(
  res: Response,
  fallback: string = DEFAULT_ERROR,
): Promise<string> {
  try {
    const body = await res.json()
    if (body?.detail != null) {
      const d = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      return toUserError(d, fallback)
    }
  } catch {
    /* ignore body parse */
  }
  if (res.status >= 500) return '服务繁忙，请稍后重试。'
  if (res.status === 413) return '文件过大，请压缩后重试。'
  if (res.status === 400 || res.status === 422) return '请求无效，请检查后重试。'
  return fallback
}
