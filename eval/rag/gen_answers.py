"""为 Ragas 生成 (question, answer, contexts, reference)：hybrid+rerank 取 top4，DeepSeek 只依据上下文作答"""
import json
from pathlib import Path
from langchain_core.messages import SystemMessage, HumanMessage
from app.llm import get_llm
from app.rag.store import KnowledgeBase
from app.rag.retrieve import hybrid_search

HERE = Path(__file__).parent


def main():
    store = KnowledgeBase(root="data/eval_kb")
    qa = json.loads((HERE / "qa.json").read_text(encoding="utf-8"))
    llm = get_llm(temperature=0)
    rows = []
    for item in qa:
        ctx = [h["text"] for h in hybrid_search(item["q"], k=4, store=store)]
        ans = llm.invoke([SystemMessage("只根据给定资料简洁回答问题（中文），资料没有的信息不要编造。"),
                          HumanMessage("资料：\n" + "\n---\n".join(ctx) + f"\n\n问题：{item['q']}")]).content
        rows.append({"user_input": item["q"], "response": ans, "retrieved_contexts": ctx,
                     "reference": item["answer"]})
        print("✓", item["q"][:30])
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "rag_answers.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
