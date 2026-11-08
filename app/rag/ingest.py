"""文档解析与切块：PDF 保留页码，MD/TXT 视为第 1 页"""
import re
from pathlib import Path
from pypdf import PdfReader

SUPPORTED = {".pdf", ".md", ".txt", ".markdown"}
CHUNK_SIZE = 500
OVERLAP = 80
SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", "；", "; ", "，", ", ", " ", ""]


def load_pages(path: Path) -> list[tuple[int, str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        reader = PdfReader(str(path))
        return [(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
    if ext in SUPPORTED:
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "gb18030"):
            try:
                return [(1, raw.decode(enc))]
            except UnicodeDecodeError:
                continue
        return [(1, raw.decode("utf-8", errors="ignore"))]
    raise ValueError(f"不支持的文件类型：{ext}")


def clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"-\n(?=[a-z])", "", text)          # PDF 断词连字符
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split(text: str, seps: list[str], size: int) -> list[str]:
    """递归切分：先按大分隔符切，超长片段再用更细的分隔符切"""
    if len(text) <= size:
        return [text]
    sep = next((s for s in seps if s and s in text), "")
    if not sep:
        return [text[i:i + size] for i in range(0, len(text), size)]
    rest = seps[seps.index(sep) + 1:]
    parts = [p + sep for p in text.split(sep)]
    out = []
    for p in parts:
        out += [p] if len(p) <= size else _split(p, rest, size)
    return out


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    pieces = _split(text, SEPARATORS, size)
    chunks, cur = [], ""
    for p in pieces:  # 合并小片段到接近 size
        if len(cur) + len(p) <= size:
            cur += p
        else:
            if cur.strip():
                chunks.append(cur.strip())
            tail = cur[-overlap:] if overlap and cur else ""
            cur = tail + p
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if len(c) >= 20]


def chunk_document(path: Path) -> list[dict]:
    out = []
    for page, text in load_pages(path):
        for c in chunk_text(clean(text)):
            out.append({"text": c, "page": page})
    return out
