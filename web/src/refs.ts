export interface Ref { title: string; url: string; local: boolean }

const URL_RE = String.raw`(https?:\/\/[^\s>)]+|\/api\/kb\/[^\s>)]+)`

/** 从"参考资料"里解析 [n] 标题 - URL（本地文档的 URL 形如 /api/kb/docs/{id}/file#page=3） */
export function parseRefs(text: string): Map<number, Ref> {
  const m = new Map<number, Ref>()
  const re = new RegExp(String.raw`^\s*[-*]?\s*\[(\d+)\]\s*(.*?)\s*[-–—:：]\s*<?` + URL_RE + '>?', 'gm')
  let x: RegExpExecArray | null
  while ((x = re.exec(text))) {
    const url = x[3]
    m.set(+x[1], { title: x[2] || url, url, local: url.startsWith('/api/kb/') })
  }
  return m
}

const LOCAL_MARK = '📄本地：'

/** 把正文里的 [n] 变成 markdown 链接；参考资料行保持原样（本地链接转成可点击链接） */
export function linkCitations(src: string, refs: Map<number, Ref>): string {
  const isRefLine = new RegExp(String.raw`^\s*[-*]?\s*\[\d+\].*` + URL_RE)
  return src.split('\n').map(line => {
    if (isRefLine.test(line)) {
      return line.replace(/(?<![(<])(\/api\/kb\/[^\s>)]+)/, (u) => `[打开原文](${u})`)
    }
    return line.replace(/\[(\d+)\](?![(:\]])/g, (all, n) => {
      const r = refs.get(+n)
      if (!r) return all
      const title = (r.local && !r.title.startsWith(LOCAL_MARK) ? LOCAL_MARK + r.title : r.title).replace(/"/g, "'")
      return `[\\[${n}\\]](${r.url} "${title}")`
    })
  }).join('\n')
}
