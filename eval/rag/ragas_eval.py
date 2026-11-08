"""Ragas 评测（在独立环境 .venv-eval 中运行，避免 ragas 依赖降级主项目的 langchain-openai）
裁判模型：DeepSeek-chat；ResponseRelevancy 需要的 embedding：DashScope text-embedding-v3
运行：.venv-eval\\Scripts\\python eval\\rag\\ragas_eval.py"""
import json
import os
from pathlib import Path

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas import EvaluationDataset, evaluate, RunConfig
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, ResponseRelevancy, LLMContextPrecisionWithReference

HERE = Path(__file__).parent
rows = json.loads((HERE / "results" / "rag_answers.json").read_text(encoding="utf-8"))
llm = LangchainLLMWrapper(ChatOpenAI(model="deepseek-chat", api_key=os.environ["DEEPSEEK_API_KEY"],
                                     base_url="https://api.deepseek.com", temperature=0, max_retries=6))
emb = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
    model="text-embedding-v3", api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    check_embedding_ctx_length=False, chunk_size=10))
res = evaluate(EvaluationDataset.from_list(rows),
               metrics=[Faithfulness(), ResponseRelevancy(strictness=1), LLMContextPrecisionWithReference()],
               llm=llm, embeddings=emb, run_config=RunConfig(max_workers=4, timeout=180))
df = res.to_pandas()
cols = [c for c in ["faithfulness", "answer_relevancy", "llm_context_precision_with_reference"] if c in df]
summary = {c: round(float(df[c].mean()), 3) for c in cols}
print(summary)
print(df[["user_input"] + cols].to_string())
(HERE / "results" / "ragas.json").write_text(json.dumps(
    {"summary": summary, "rows": df[["user_input"] + cols].to_dict("records")}, ensure_ascii=False, indent=1),
    encoding="utf-8")
