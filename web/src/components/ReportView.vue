<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownIt from 'markdown-it'
import { state } from '../store'
import { parseRefs, linkCitations } from '../refs'

const md = new MarkdownIt({ html: false, linkify: true, breaks: false })

function isSafeHref(href: string): boolean {
  if (/^\/api\/kb\/docs\/[A-Za-z0-9_-]+\/file(?:#page=\d+)?$/.test(href)) return true
  try {
    const u = new URL(href)
    return u.protocol === 'http:' || u.protocol === 'https:'
  } catch {
    return false
  }
}
;(md as unknown as { validateLink: (url: string) => boolean }).validateLink = isSafeHref

const dflt = md.renderer.rules.link_open || ((t, i, o, _e, s) => s.renderToken(t, i, o))
md.renderer.rules.link_open = (t, i, o, e, s) => {
  const href = String(t[i].attrGet('href') || '')
  if (!isSafeHref(href)) return dflt(t, i, o, e, s)
  t[i].attrSet('target', '_blank')
  t[i].attrSet('rel', 'noopener noreferrer')
  return dflt(t, i, o, e, s)
}

const refs = computed(() => parseRefs(state.report))
const html = computed(() => md.render(linkCitations(state.report, refs.value)))
const copied = ref(false)

async function copy() {
  try { await navigator.clipboard.writeText(state.report) } catch {
    const t = document.createElement('textarea')
    t.value = state.report
    document.body.appendChild(t)
    t.select()
    document.execCommand('copy')
    t.remove()
  }
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1500)
}
function download() {
  const blob = new Blob([state.report], { type: 'text/markdown;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `research-${state.threadId || 'report'}.md`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <section class="rp card" aria-labelledby="report-title">
    <div class="bar">
      <h3 id="report-title">研究报告
        <span v-if="state.phase === 'running' && state.report" class="muted">已有草稿，审稿尚未结束</span>
        <span v-else-if="state.phase === 'done' && state.report" class="muted">当前文本</span>
      </h3>
      <div class="btns" v-if="state.report">
        <button type="button" class="btn" @click="copy">{{ copied ? '已复制' : '复制 Markdown' }}</button>
        <button type="button" class="btn" @click="download">下载 .md</button>
      </div>
    </div>
    <div v-if="!state.report" class="ph">
      <div class="sk" v-for="i in 4" :key="i" :style="{ width: 55 + (i * 13) % 35 + '%' }" />
      <p>{{ state.phase === 'running' ? '报告还没从撰写节点返回。界面不会预写正文。' : '完成后，带编号引用的报告会显示在这里。' }}</p>
    </div>
    <article v-else class="md" v-html="html" />
  </section>
</template>
