from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from app.llm import get_llm
from app.utils import today_hint


class Review(BaseModel):
    score: int = Field(description="报告质量总分，1到10")
    passed: bool = Field(description="是否达到可交付标准（score>=7 且无严重事实错误）")
    issues: list[str] = Field(description="发现的具体问题，如遗漏、无引用论断、矛盾、过时信息")
    follow_up_questions: list[str] = Field(
        description="为弥补问题需要补充检索的新子问题，最多3个；通过时为空")


SYSTEM_PROMPT = """你是一名研究报告审稿人。按下面的评分细则打分（每项0到2.5分，总分10分，四舍五入为整数）：
1. 覆盖度：原问题的各个方面是否都有回答；
2. 证据：关键论断是否有 [n] 引用，引用是否与论断相关；
3. 一致性：有无明显前后矛盾；
4. 结论：是否给出了明确、可操作的结论或建议。

注意：
- 来自技术博客、媒体文章的引用是可以接受的，不要求必须是官方文档；
- 总分 >=7 且没有严重事实错误即判定通过；
- issues 只列最重要的问题，最多5条，每条一句话；
- 只有需要新资料才能解决的问题才给补充子问题，最多3个；纯写作问题不要补查。"""


def review_report(question: str, report: str, covered: list[str]) -> Review:
    llm = get_llm(temperature=0).with_structured_output(Review, method="function_calling")
    return llm.invoke([
        SystemMessage(SYSTEM_PROMPT + "\n" + today_hint()),
        HumanMessage(f"原始问题：{question}\n\n已研究的子问题：\n" + "\n".join(f"- {q}" for q in covered)
                     + f"\n\n报告：\n{report}"),
    ])

