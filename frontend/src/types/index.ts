export interface ChartSpec {
  chart_id?: string
  title?: string
  option: Record<string, unknown>
  evidence?: Record<string, unknown>
}

export interface MessageAttachment {
  url: string
  object_key?: string
  mime_type?: string
  name?: string
  /** 本地预览用（blob:），不入库 */
  previewUrl?: string
  /** 小红书有序图文卡片 / 销售图表 */
  kind?: 'xhs_card' | 'chart'
  index?: number
  title?: string
  body?: string
  tags?: string[]
  image_url?: string
  error?: string
  chart_id?: string
  option?: Record<string, unknown>
  evidence?: Record<string, unknown>
}

export interface ApprovalDraft {
  to?: string
  subject?: string
  body?: string
  [key: string]: string | undefined
}

export interface MessageApproval {
  /** pending=等待确认；approved/cancelled=已结束（卡片可隐藏） */
  status: 'pending' | 'approved' | 'cancelled'
  question: string
  action?: string
  /** 可编辑草稿（如 send_email） */
  draft?: ApprovalDraft
  editable?: boolean
  fields?: string[]
}

export interface XhsCard {
  index: number
  title: string
  body: string
  tags: string[]
  image_url?: string
  error?: string
}

export interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
  attachments?: MessageAttachment[]
  /** 有序小红书图文（SSE 实时 + 落库回读） */
  xhsCards?: XhsCard[]
  /** 销售分析 ECharts（SSE 实时 + 落库回读） */
  charts?: ChartSpec[]
  /** 本轮回答总 token（仅 assistant；SSE / 落库后回读） */
  usage?: TokenUsage
  /** 敏感操作气泡内确认（不落库，仅会话内） */
  approval?: MessageApproval
}

export interface TokenUsage {
  total_tokens: number
}

/** 与后端 INTENTS 对齐；前端按钮强制路由用 */
export type ClientIntent =
  | 'chat'
  | 'rag'
  | 'media_gen'
  | 'xhs_pack'
  | 'image_edit'
  | 'speech_recognize'


export interface Conversation {
  id: string
  threadId: string
  title: string
  messages: Message[]
  createdAt: number
  updatedAt: number
}

export interface SSEPayload {
  type: 'text' | 'status' | 'interrupt' | 'done' | 'title' | 'error' | 'usage' | 'file' | 'xhs_card' | 'chart'
  content?: string
  title?: string | null
  total_tokens?: number
  name?: string
  url?: string
  object_key?: string
  mime_type?: string
  file_size?: number
  index?: number
  body?: string
  tags?: string[]
  image_url?: string
  error?: string
  chart_id?: string
  option?: Record<string, unknown>
  evidence?: Record<string, unknown>
  /** 用户主动停止生成 */
  stopped?: boolean
  data?: {
    question: string
    action?: string
    kind?: string
    draft?: ApprovalDraft
    editable?: boolean
    fields?: string[]
    args?: Record<string, unknown>
  }
}

export type ParseStatus = 'parsing' | 'done' | 'failed'

export interface KBFile {
  id: number
  file_name: string
  file_url: string
  file_size: number
  file_type: string
  object_key: string
  parse_status: ParseStatus
  parse_error?: string
  created_at: string
}

export interface ChatUploadResult {
  url: string
  display_url: string
  object_key: string
  mime_type: string
  name: string
  file_size: number
}
