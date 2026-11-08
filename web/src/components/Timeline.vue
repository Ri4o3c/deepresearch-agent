<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { state } from '../store'

const box = ref<HTMLElement>()
watch(() => state.logs.length, async () => { await nextTick(); box.value?.scrollTo({ top: box.value.scrollHeight }) })
const kindLabel: Record<string, string> = {
  start: '开始', planner: '规划', researcher: '检索', writer: '撰写',
  reviewer: '审稿', loop: '补查', done: '完成', error: '中断',
}
</script>

<template>
  <section class="tl card" aria-labelledby="log-title">
    <h3 id="log-title">过程
      <small v-if="state.startedAt" class="muted">{{ state.elapsed.toFixed(1) }} 秒</small>
    </h3>
    <div ref="box" class="list" aria-live="polite">
      <p v-if="!state.logs.length" class="muted">提交问题后，这里只记录后端实际推送的事件。</p>
      <div v-for="(l, i) in state.logs" :key="i" class="item">
        <span class="t">{{ l.t.toFixed(1) }}s</span>
        <span class="kind">{{ kindLabel[l.kind] || l.kind }}</span>
        <span>{{ l.text }}</span>
      </div>
    </div>
  </section>
</template>
