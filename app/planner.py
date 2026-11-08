from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from app.llm import get_llm
from app.utils import today_hint


class SubQuestion(BaseModel):
    question: str = Field(description="一个具体、可独立检索的子问题")
    reason: str = Field(description="为什么需要研究这个子问题")


class ResearchPlan(BaseModel):
    topic: str = Field(description="对研究主题的一句话概括")
    sub_questions: list[SubQuestion] = Field(description="3到6个子问题")


SYSTEM_PROMPT = """你是一名资深研究规划师。
把用户的研究问题拆成3到6个子问题，要求：
1. 每个子问题都能单独上网检索；
2. 子问题之间不重复，合起来能完整回答原问题；
3. 按"背景 -> 现状 -> 对比/分析 -> 趋势"的逻辑排序；
4. 每个子问题必须紧扣原问题的核心对象，不要发散到相邻领域。"""


def plan_research(question: str) -> ResearchPlan:
    llm = get_llm().with_structured_output(ResearchPlan, method="function_calling")
    return llm.invoke([SystemMessage(SYSTEM_PROMPT + "\n" + today_hint()), HumanMessage(question)])


if __name__ == "__main__":
    plan = plan_research("2026年主流AI Agent开发框架有哪些，各自优缺点是什么？")
    print("主题:", plan.topic)
    for i, sq in enumerate(plan.sub_questions, 1):
        print(f"{i}. {sq.question}\n   理由: {sq.reason}")
