<script setup lang="ts">
import { computed } from 'vue'
import { state } from '../store'

const rounds = computed(() => {
  const m = new Map<number, typeof state.workers>()
  for (const w of state.workers) { if (!m.has(w.round)) m.set(w.round, []); m.get(w.round)!.push(w) }
  return [...m.entries()].sort((a, b) => a[0] - b[0])
})
const lastReview = computed(() => state.reviews[state.reviews.length - 1])
const researchStatus = computed(() => {
  const cur = state.workers.filter(w => w.round === state.round)
  if (!cur.length) return 'wait'
  return cur.some(w => w.status === 'active') ? 'active' : cur.every(w => w.status === 'done') ? 'done' : 'wait'
})
const label: Record<string, string> = { wait: '等待', active: '进行中', done: '完成' }
</script>

<template>
  <section class="flow card" aria-labelledby="flow-title">
    <div class="head">
      <h3 id="flow-title">协作状态</h3>
      <div class="rounds">
        <span v-for="r in 3" :key="r" class="dot" :class="{ on: r <= state.round }">
          {{ r === 1 ? '初稿' : '补查 ' + (r - 1) }}
        </span>
        <span class="muted">最多两轮补查</span>
      </div>
    </div>

    <div class="pipe">
      <div class="node" :class="state.planner">
        <div class="name">规划</div>
        <div class="sub">Planner</div>
        <div class="st">{{ label[state.planner] }}</div>
      </div>
      <div class="edge" :class="{ lit: state.planner === 'done' }" />
      <div class="workers">
        <div class="wtitle">检索
          <small>本轮 {{ state.workers.filter(w => w.round === state.round).length || 0 }} 个</small>
        </div>
        <p v-if="!state.workers.length" class="muted">规划完成后，这里列出实际子问题。</p>
        <div v-for="[r, ws] in rounds" :key="r" class="wround">
          <div class="rtag">第 {{ r }} 轮</div>
          <div class="wlist">
            <div v-for="w in ws" :key="w.id" class="wcard" :class="w.status">
              <span class="ok" v-if="w.status === 'done'" aria-label="完成">✓</span>
              <span class="spin" v-else-if="w.status === 'active'" aria-hidden="true" />
              <p>{{ w.question }}</p>
              <div class="meta" v-if="w.status === 'done' && w.statsKnown === false">历史快照未提供来源与发现数</div>
              <div class="meta" v-else-if="w.status === 'done' && (w.sources || w.findings)">
                <span>{{ w.sources }} 个来源</span>
                <span v-if="w.local">其中本地 {{ w.local }}</span>
                <span>{{ w.findings }} 条发现</span>
              </div>
              <div class="meta" v-else-if="w.status === 'done'">这次没有留下来源</div>
            </div>
          </div>
        </div>
      </div>
      <div class="edge" :class="{ lit: researchStatus === 'done' }" />
      <div class="node" :class="state.writer">
        <div class="name">撰写</div>
        <div class="sub">Writer</div>
        <div class="st">{{ label[state.writer] }}</div>
      </div>
      <div class="edge" :class="{ lit: state.writer === 'done' }" />
      <div class="node" :class="state.reviewer">
        <div class="name">审稿</div>
        <div class="sub">Reviewer</div>
        <div v-if="lastReview" class="score" :class="{ pass: lastReview.passed }">
          {{ lastReview.score }}<small>/10</small>
        </div>
        <div class="st" v-else>{{ label[state.reviewer] }}</div>
      </div>
    </div>
    <p v-if="state.round > 1" class="loopTxt">审稿未通过，已进入第 {{ state.round }} 轮补查。</p>
    <div v-if="state.reviews.length" class="reviews">
      <div v-for="(r, i) in state.reviews" :key="i" class="rv" :class="{ pass: r.passed }">
        <b>第 {{ i + 1 }} 轮 {{ r.score }}/10 {{ r.passed ? '通过' : '未通过' }}</b>
        <ul><li v-for="(x, j) in r.issues" :key="j">{{ x }}</li></ul>
      </div>
    </div>
    <p v-else-if="state.phase === 'idle'" class="muted">尚未开始。空闲时不会显示分数或来源数。</p>
  </section>
</template>
