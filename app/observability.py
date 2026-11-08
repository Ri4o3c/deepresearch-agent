"""可观测性：配置了 LANGFUSE_* 时把 LangChain/LangGraph 调用链上报到 Langfuse，否则全部是空操作。
任何 Langfuse 异常（未安装、服务不可达）都只记日志，绝不影响研究主流程。"""
import logging
import os
from contextlib import contextmanager

log = logging.getLogger("observability")
_client = None


def enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def _get_client():
    global _client
    if _client is None:
        from langfuse import get_client
        _client = get_client()
    return _client


def get_callbacks() -> list:
    if not enabled():
        return []
    try:
        from langfuse.langchain import CallbackHandler
        return [CallbackHandler()]
    except Exception as e:  # noqa: BLE001
        log.warning("Langfuse CallbackHandler 初始化失败，已跳过：%s", e)
        return []


def build_config(thread_id: str, question: str | None = None, tags: list[str] | None = None) -> dict:
    cfg: dict = {"configurable": {"thread_id": thread_id}}
    cbs = get_callbacks()
    if cbs:
        cfg["callbacks"] = cbs
        cfg["metadata"] = {"langfuse_session_id": thread_id, "langfuse_tags": tags or [],
                           "question": (question or "")[:200]}
    return cfg


@contextmanager
def span(name: str, as_type: str = "span", **inputs):
    """包裹检索等非 LangChain 调用；关闭或出错时退化为空上下文"""
    cm = None
    if enabled():
        try:
            cm = _get_client().start_as_current_observation(name=name, as_type=as_type, input=inputs)
            cm.__enter__()
        except Exception as e:  # noqa: BLE001
            log.warning("Langfuse span %s 创建失败：%s", name, e)
            cm = None
    try:
        yield
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as e:  # noqa: BLE001
                log.warning("Langfuse span %s 结束失败：%s", name, e)


def flush() -> None:
    """服务关闭时把缓冲的 trace 发出去；未启用时直接返回"""
    if not enabled():
        return
    try:
        _get_client().flush()
    except Exception as e:  # noqa: BLE001
        log.warning("Langfuse flush 失败：%s", e)
