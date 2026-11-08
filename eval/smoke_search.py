"""在线冒烟：每个 provider 搜一次（不调大模型）"""
import time
from app import search as S
for name in ["searxng", "ddgs"]:
    for q in ["reciprocal rank fusion", "LangGraph 多智能体"]:
        t = time.time()
        try:
            out = S.web_search(q, 5, providers=[S.PROVIDERS[name]()])
            full = sum(len(r["content"]) > 400 for r in out)
            print(f"{name} [{q}] n={len(out)} 用时={time.time()-t:.1f}s 全文>400字={full} top={out[0]['url'][:60] if out else None}")
        except Exception as e:
            print(f"{name} [{q}] 失败 {type(e).__name__}: {str(e)[:100]}  用时={time.time()-t:.1f}s")