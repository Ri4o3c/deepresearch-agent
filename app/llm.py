import os
from langchain_openai import ChatOpenAI

def get_llm(temperature: float = 0.3, *, max_retries: int = 6,
            max_tokens: int | None = None) -> ChatOpenAI:
    extra_body: dict = {"thinking": {"type": "disabled"}}
    if max_tokens is not None:
        # ChatOpenAI 会将普通 max_tokens 改名；DeepSeek 的契约仍使用 max_tokens。
        extra_body["max_tokens"] = max_tokens
    return ChatOpenAI(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
        temperature=temperature,
        extra_body=extra_body,
        max_retries=max_retries,  # 评测可以禁用重试，按真实请求逐次记账
    )
