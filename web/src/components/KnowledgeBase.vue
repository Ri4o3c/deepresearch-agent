<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { listDocs, deleteDoc, uploadFiles, abortUpload, precheck, ACCEPT, type KbDoc } from '../kb'

const docs = ref<KbDoc[]>([])
const totalChunks = ref(0)
const open = ref(true)
const over = ref(false)
const phase = ref<'idle' | 'uploading' | 'indexing'>('idle')
const progress = ref(0)
const msgs = ref<{ ok: boolean; text: string }[]>([])
const removing = ref<string | null>(null)
const input = ref<HTMLInputElement>()
const loaded = ref(false)
const listError = ref('')
const busy = computed(() => phase.value !== 'idle')

async function refresh() {
  try {
    const r = await listDocs()
    docs.value = r.docs.slice().reverse()
    totalChunks.value = r.total_chunks
    listError.value = ''
  } catch (e: any) {
    listError.value = e.message
  } finally {
    loaded.value = true
  }
}
onMounted(refresh)
onBeforeUnmount(() => abortUpload())

function flash(list: { ok: boolean; text: string }[]) {
  msgs.value = list
  window.setTimeout(() => { if (msgs.value === list) msgs.value = [] }, 6000)
}

async function handle(files: File[]) {
  if (busy.value || !files.length) return
  const { ok, bad } = precheck(files)
  const out = bad.map(text => ({ ok: false, text }))
  if (!ok.length) return flash(out)
  phase.value = 'uploading'
  progress.value = 0
  try {
    const res = await uploadFiles(ok, p => (progress.value = p), () => (phase.value = 'indexing'))
    for (const r of res) {
      if (r.ok && r.doc) out.push({ ok: true, text: `${r.filename}：${r.doc.pages} 页，${r.doc.chunks} 个片段` })
      else out.push({ ok: false, text: `${r.filename}：${r.error || '未入库'}` })
    }
  } catch (e: any) {
    out.push({ ok: false, text: e.message })
  }
  phase.value = 'idle'
  await refresh()
  flash(out)
}

function onDrop(e: DragEvent) { over.value = false; handle([...(e.dataTransfer?.files ?? [])]) }
function onPick(e: Event) { const t = e.target as HTMLInputElement; handle([...(t.files ?? [])]); t.value = '' }

async function remove(d: KbDoc) {
  if (!confirm(`从知识库删除「${d.filename}」？此操作调用删除接口。`)) return
  removing.value = d.id
  try { await deleteDoc(d.id); await refresh() }
  catch (e: any) { flash([{ ok: false, text: e.message }]) }
  removing.value = null
}
const icon = (ext: string) => (ext === '.pdf' ? 'PDF' : ext === '.txt' ? 'TXT' : 'MD')
const size = (b: number) => (b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB')
</script>

<template>
  <section class="kb card">
    <button type="button" class="head" :aria-expanded="open" @click="open = !open">
      <span class="title">本地知识库
        <small class="muted">{{ listError ? '列表读取失败' : loaded ? `${docs.length} 个文档，${totalChunks} 个片段` : '正在读取' }}</small>
      </span>
      <span class="muted">{{ open ? '收起' : '展开' }}</span>
    </button>
    <div v-if="open" class="body">
      <p v-if="loaded && !listError" class="muted">{{ docs.length ? '研究时会同时检索网页和这些文档。' : '库为空时只检索网页。' }}</p>
      <button type="button" class="drop" :disabled="busy" @dragover.prevent="over = true"
        @dragleave.prevent="over = false" @drop.prevent="onDrop" @click="input?.click()">
        <template v-if="!busy">
          <p><b>添加 PDF、Markdown 或 TXT</b></p>
          <p class="muted">单个不超过 20MB。选择后才会上传。</p>
        </template>
        <template v-else>
          <p>{{ phase === 'uploading' ? `正在上传 ${Math.round(progress * 100)}%` : '正在解析、切块和向量化' }}</p>
          <div class="bar" role="progressbar" :aria-valuenow="phase === 'indexing' ? undefined : Math.round(progress * 100)">
            <i :style="{ width: (phase === 'indexing' ? 100 : progress * 100) + '%' }" />
          </div>
        </template>
      </button>
      <input ref="input" type="file" multiple :accept="ACCEPT.join(',')" hidden @change="onPick" />
      <div class="msgs" aria-live="polite">
        <p v-for="m in msgs" :key="m.text" class="m" :class="{ bad: !m.ok }">{{ m.text }}</p>
      </div>
      <p v-if="!loaded" class="muted">正在读取文档列表。</p>
      <p v-else-if="listError" class="m bad" role="alert">列表没有读到：{{ listError }}</p>
      <p v-else-if="!docs.length" class="muted">还没有文档。</p>
      <ul v-else class="docs">
        <li v-for="d in docs" :key="d.id">
          <span class="ic">{{ icon(d.ext) }}</span>
          <a :href="`/api/kb/docs/${d.id}/file`" target="_blank" rel="noopener noreferrer">{{ d.filename }}</a>
          <span class="muted">{{ d.pages }} 页 · {{ d.chunks }} 片段 · {{ size(d.size) }}</span>
          <button type="button" class="text" :disabled="busy || !!removing" @click="remove(d)">删除</button>
        </li>
      </ul>
    </div>
  </section>
</template>
