"""可观测性模块测试：未配置时为空操作；配置假密钥且服务不可达时不报错、不阻塞（不访问外网）"""
import os
import time
import unittest
from unittest import mock

from app import observability as obs

KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL")


class NoopTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.dict(os.environ, {}, clear=False)
        self.p.start()
        for k in KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        self.p.stop()

    def test_disabled(self):
        self.assertFalse(obs.enabled())
        self.assertEqual(obs.get_callbacks(), [])
        cfg = obs.build_config("t1", "问题", ["x"])
        self.assertEqual(cfg, {"configurable": {"thread_id": "t1"}})
        with obs.span("kb_search", query="q"):
            pass
        obs.flush()

    def test_span_propagates_user_error(self):
        with self.assertRaises(ValueError):
            with obs.span("x"):
                raise ValueError("业务异常应原样抛出")


class FakeKeysTest(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "pk-lf-fake", "LANGFUSE_SECRET_KEY": "sk-lf-fake",
                                              "LANGFUSE_HOST": "http://127.0.0.1:9", "LANGFUSE_BASE_URL": "http://127.0.0.1:9"})
        self.p.start()
        obs._client = None

    def tearDown(self):
        self.p.stop()

    def test_enabled_config(self):
        t = time.time()
        cfg = obs.build_config("t2", "q" * 500, ["api", "start"])
        self.assertIn("CallbackHandler", type(cfg["callbacks"][0]).__name__)
        self.assertEqual(cfg["metadata"]["langfuse_session_id"], "t2")
        self.assertEqual(cfg["metadata"]["langfuse_tags"], ["api", "start"])
        self.assertEqual(len(cfg["metadata"]["question"]), 200)
        with obs.span("tavily_search", as_type="tool", query="q"):
            pass
        obs.flush()
        self.assertLess(time.time() - t, 10)

    def test_graph_runs_with_callbacks(self):
        """一个不调大模型的小图带着 Langfuse callback 运行，确认不会因上报失败而报错"""
        from typing import TypedDict
        from langgraph.graph import StateGraph, START, END

        class S(TypedDict):
            x: int

        g = StateGraph(S)
        g.add_node("inc", lambda s: {"x": s["x"] + 1})
        g.add_edge(START, "inc")
        g.add_edge("inc", END)
        cfg = obs.build_config("t3", "q", ["test"])
        cfg.pop("configurable")
        self.assertEqual(g.compile().invoke({"x": 1}, cfg)["x"], 2)
        obs.flush()


if __name__ == "__main__":
    unittest.main()