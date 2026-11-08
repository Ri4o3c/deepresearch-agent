// 知识库接口：上传用 XMLHttpRequest（fetch 拿不到上传进度）
export interface KbDoc { id: string; filename: string; ext: string; chunks: number; pages: number
  size: number; created_at: number; ingest_seconds: number }
export interface UploadResult { filename: string; ok: boolean; error?: string; doc?: KbDoc }

const BASE = '/api/kb'
export const ACCEPT = ['.pdf', '.md', '.markdown', '.txt']
export const MAX_MB = 20

async function json(res: Response) {
  let j: any = null
  try { j = await res.json() } catch { /* 非 JSON */ }
  if (!res.ok) throw new Error(typeof j?.detail === 'string' ? j.detail
    : res.status >= 502 ? '后端服务未启动' : `请求失败（HTTP ${res.status}）`)
  return j
}

export async function listDocs(): Promise<{ docs: KbDoc[]; total_chunks: number }> {
  return json(await fetch(`${BASE}/docs`))
}

export async function deleteDoc(id: string) {
  return json(await fetch(`${BASE}/docs/${id}`, { method: 'DELETE' }))
}

let activeUpload: XMLHttpRequest | null = null

/** 离开页面时中止未完成上传，不改接口路径。 */
export function abortUpload() {
  activeUpload?.abort()
  activeUpload = null
}

/** onProgress(0~1)：上传阶段的字节进度；上传完成后服务端还要解析+向量化 */
export function uploadFiles(files: File[], onProgress: (p: number) => void,
                            onUploaded: () => void): Promise<UploadResult[]> {
  return new Promise((resolve, reject) => {
    const fd = new FormData()
    files.forEach(f => fd.append('files', f))
    const xhr = new XMLHttpRequest()
    activeUpload = xhr
    xhr.open('POST', `${BASE}/upload`)
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total) }
    xhr.upload.onload = () => { onProgress(1); onUploaded() }
    xhr.onerror = () => { activeUpload = null; reject(new Error('网络错误：无法连接后端')) }
    xhr.onabort = () => { activeUpload = null; reject(new Error('上传已取消')) }
    xhr.onload = () => {
      activeUpload = null
      let j: any = null
      try { j = JSON.parse(xhr.responseText) } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(j.results)
      else reject(new Error(typeof j?.detail === 'string' ? j.detail
        : xhr.status >= 502 ? '后端服务未启动' : `上传失败（HTTP ${xhr.status}）`))
    }
    xhr.send(fd)
  })
}

/** 前端预检：类型和大小 */
export function precheck(files: File[]): { ok: File[]; bad: string[] } {
  const ok: File[] = [], bad: string[] = []
  for (const f of files) {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase()
    if (!ACCEPT.includes(ext)) bad.push(`${f.name}：不支持的类型`)
    else if (f.size > MAX_MB * 1024 * 1024) bad.push(`${f.name}：超过 ${MAX_MB}MB`)
    else if (f.size === 0) bad.push(`${f.name}：空文件`)
    else ok.push(f)
  }
  return { ok, bad }
}
