/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

declare module 'markdown-it-multimd-table' {
  import type MarkdownIt from 'markdown-it'
  interface MultimdTableOptions {
    multiline?: boolean
    rowspan?: boolean
    headerless?: boolean
    multibody?: boolean
    autolabel?: boolean
  }
  const multimdTable: MarkdownIt.PluginWithOptions<MultimdTableOptions>
  export default multimdTable
}
