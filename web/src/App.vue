<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AgentFlow from './components/AgentFlow.vue'
import Timeline from './components/Timeline.vue'
import ReportView from './components/ReportView.vue'
import KnowledgeBase from './components/KnowledgeBase.vue'
import { state, startResearch, resume, cancel, loadThread, removeHistory } from './store'
import { health } from './api'

const q = ref('')
const online = ref<boolean | null>(null)
const showHistory = ref(false)
const examples = [
  'LangGraph、CrewAI、AutoGen 三个多智能体框架怎么选？',
  '2026 年国产大模型 API 价格对比',
  'RAG 中混合检索和重排序能带来多大提升？',
  '固态电池商业化进展到哪一步了？',
]
const running = computed(() => state.phase === 'running')
const len = computed(() => q.value.trim().length)
const canSubmit = computed(() => !running.value && online.value !== false && len.value >= 2 && len.value <= 500)
let poll: number | undefined

async function ping() { online.value = await health() }
onMounted(() => { ping(); poll = window.setInterval(ping, 10000) })
onBeforeUnmount(() => { if (poll !== undefined) window.clearInterval(poll) })

function submit() {
  if (!canSubmit.value) return
  startResearch(q.value)
}
function pick(e: string) { q.value = e }
function fmt(t: number) { return new Date(t).toLocaleString('zh-CN', { hour12: false }) }
function open(id: string) { showHistory.value = false; loadThread(id) }
const phaseLabel: Record<string, string> = { done: '完成', error: '未完成', running: '进行中', idle: '空闲' }
</script>

<template>
  <div class="app">
    <header class="top">
      <div class="brand">
        <strong>DeepResearch</strong>
        <span>中文研究工作台 · 规划、检索、撰写、审稿</span>
      </div>
      <div class="top-actions">
        <span class="pill" :class="{ ok: online === true, bad: online === false }" role="status">
          {{ online === null ? '正在检查后端' : online ? '后端在线' : '后端离线' }}
        </span>
        <button type="button" class="btn" :aria-expanded="showHistory" @click="showHistory = !showHistory">
          历史 {{ state.history.length }}
        </button>
      </div>
    </header>

    <section v-if="showHistory" class="history card" aria-label="历史任务">
      <p v-if="!state.history.length" class="muted">还没有历史任务。完成或中断一次研究后会记在这台浏览器里。</p>
      <div v-for="h in state.history" :key="h.id" class="hrow">
        <span class="tag" :class="h.status">{{ phaseLabel[h.status] || h.status }}</span>
        <button type="button" class="question" @click="open(h.id)">{{ h.question }}</button>
        <code class="id">#{{ h.id }}</code>
        <span class="muted">{{ fmt(h.at) }}</span>
        <button type="button" class="text" @click="removeHistory(h.id)">从列表移除</button>
      </div>
    </section>

    <section class="composer card">
      <h1>从一个问题开始</h1>
      <p class="lead">系统按现有流程检索网页和本地知识库，写出带编号引用的报告，再由审稿决定是否补查。这里不显示没有来源的统计。</p>
      <form class="ask" @submit.prevent="submit">
        <label for="question">研究问题</label>
        <textarea id="question" v-model="q" rows="3" maxlength="500" :disabled="running"
          placeholder="至少 2 个字，最多 500 个字"
          @keydown.enter.exact.prevent="submit" />
        <div class="actions">
          <span class="count" :class="{ bad: len > 0 && len < 2 }">{{ len }}/500</span>
          <div>
            <button v-if="running" type="button" class="btn danger" @click="cancel">停止接收</button>
            <button v-else type="submit" class="btn primary" :disabled="!canSubmit">开始研究</button>
          </div>
        </div>
        <p v-if="online === false" class="muted" role="status">后端离线时不能新开研究。已有任务仍可在历史里查看；续跑也需要后端。</p>
        <p v-else-if="len > 0 && len < 2" class="muted">问题至少 2 个字，与接口限制一致。</p>
      </form>
      <div class="chips">
        <span class="muted">示例</span>
        <button v-for="e in examples" :key="e" type="button" class="chip" :disabled="running" @click="pick(e)">{{ e }}</button>
      </div>
    </section>

    <KnowledgeBase />

    <section v-if="state.phase === 'error'" class="banner card error" role="alert">
      <div>
        <strong>这次研究没有完成</strong>
        <p>{{ state.error }}</p>
      </div>
      <button v-if="state.resumable" type="button" class="btn primary" :disabled="running" @click="resume()">从断点续跑</button>
    </section>

    <p v-if="state.question && state.phase !== 'idle'" class="qline">
      <span class="muted">当前问题</span> {{ state.question }}
      <code v-if="state.threadId" class="id">#{{ state.threadId }}</code>
      <span class="tag" :class="state.phase">{{ phaseLabel[state.phase] }}</span>
    </p>

    <main class="workspace">
      <AgentFlow />
      <Timeline />
      <ReportView class="report" />
    </main>
    <footer class="note">规划 → 并行检索（网页与本地知识库）→ 撰写 → 审稿。补查最多两轮，由审稿结果决定，不是固定进度。</footer>
  </div>
</template>
