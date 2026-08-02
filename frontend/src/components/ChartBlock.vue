<template>
  <div class="chart-block">
    <div class="chart-block-toolbar">
      <span class="chart-block-title">{{ title || '图表' }}</span>
      <button
        type="button"
        class="chart-download-btn"
        title="下载 PNG"
        @click="downloadPng"
      >
        下载图
      </button>
    </div>
    <div ref="elRef" class="chart-block-canvas"></div>
  </div>
</template>

<script setup lang="ts">
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
])

const props = defineProps<{
  option: Record<string, unknown>
  title?: string
}>()

const elRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let ro: ResizeObserver | null = null
let pendingInit = false
let usedWindowResize = false

function onWindowResize() {
  chart?.resize()
}

function ensureChart(): echarts.ECharts | null {
  const el = elRef.value
  if (!el) return null
  // 容器尚未布局完成时不要 init，避免宽度量错导致两侧被裁
  if (el.clientWidth < 8 || el.clientHeight < 8) {
    if (!pendingInit) {
      pendingInit = true
      requestAnimationFrame(() => {
        pendingInit = false
        void render()
      })
    }
    return null
  }
  if (!chart) {
    chart = echarts.init(el, undefined, { renderer: 'canvas' })
  }
  return chart
}

async function render() {
  await nextTick()
  // 再等一帧，确保父级 width:100% 布局完成后再量宽
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  const inst = ensureChart()
  if (!inst) return
  inst.setOption(props.option || {}, { notMerge: true, lazyUpdate: false })
  requestAnimationFrame(() => {
    inst.resize()
  })
}

function downloadPng() {
  if (!chart) return
  chart.resize()
  const url = chart.getDataURL({
    type: 'png',
    pixelRatio: 2,
    backgroundColor: '#fff',
  })
  const a = document.createElement('a')
  a.href = url
  a.download = `${(props.title || 'chart').replace(/[\\/:*?"<>|]/g, '_')}.png`
  a.click()
}

onMounted(() => {
  void render()
  if (typeof ResizeObserver !== 'undefined' && elRef.value) {
    ro = new ResizeObserver(() => {
      if (!chart) {
        void render()
        return
      }
      chart.resize()
    })
    ro.observe(elRef.value)
  } else {
    usedWindowResize = true
    window.addEventListener('resize', onWindowResize)
  }
})

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
  if (usedWindowResize) {
    window.removeEventListener('resize', onWindowResize)
  }
  chart?.dispose()
  chart = null
})

watch(
  () => props.option,
  () => {
    void render()
  },
  { deep: true },
)
</script>
