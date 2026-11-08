"""搜索提供方单测：HTTP 全部 mock，不访问网络"""
import unittest
from unittest import mock

import httpx

from app import search as S


class Fake:
    def __init__(self, name, rows=None, err=None):
        self.name, self.rows, self.err, self.calls = name, rows, err, 0

    def search(self, q, n):
        self.calls += 1
        if self.err:
            raise self.err
        return [dict(r) for r in self.rows]


ROW = {"title": "t", "url": "https://a.com", "content": "摘要", "score": 0.8}


class SearXNGTest(unittest.TestCase):
    def test_parse_and_normalize(self):
        data = {"results": [{"title": "A", "url": "https://a", "content": "a", "score": 4.0},
                            {"title": "B", "url": "https://b", "content": "b", "score": 1.0},
                            {"title": "无链接", "content": "x", "score": 9}]}
        tr = httpx.MockTransport(lambda req: httpx.Response(200, json=data))
        real = httpx.Client
        with mock.patch.object(S.httpx, "Client", lambda **kw: real(transport=tr, **kw)):
            rows = S.SearXNGProvider("http://x").search("q", 5)
        self.assertEqual([r["url"] for r in rows], ["https://a", "https://b"])
        self.assertEqual(rows[0]["score"], 1.0)
        self.assertTrue(all(0 <= r["score"] <= 1 for r in rows))
        self.assertTrue(all(r["score"] >= 0.3 for r in rows))

    def test_http_error_raises(self):
        tr = httpx.MockTransport(lambda req: httpx.Response(403))
        real = httpx.Client
        with mock.patch.object(S.httpx, "Client", lambda **kw: real(transport=tr, **kw)):
            with self.assertRaises(httpx.HTTPStatusError):
                S.SearXNGProvider("http://x").search("q", 5)


class FallbackTest(unittest.TestCase):
    def test_error_then_empty_then_ok(self):
        a, b, c = Fake("a", err=RuntimeError("down")), Fake("b", rows=[]), Fake("c", rows=[ROW])
        out = S.web_search("q", providers=[a, b, c], fetch=False)
        self.assertEqual((a.calls, b.calls, c.calls), (1, 1, 1))
        self.assertEqual(out[0]["provider"], "c")
        self.assertEqual(set(out[0]), {"id", "title", "url", "content", "score", "type", "provider"})

    def test_first_ok_skips_rest(self):
        a, b = Fake("a", rows=[ROW]), Fake("b", rows=[ROW])
        S.web_search("q", providers=[a, b], fetch=False)
        self.assertEqual(b.calls, 0)

    def test_all_fail_raises(self):
        with self.assertRaises(RuntimeError):
            S.web_search("q", providers=[Fake("a", err=RuntimeError("x"))], fetch=False)

    def test_order_env(self):
        with mock.patch.dict("os.environ", {"SEARCH_PROVIDERS": "ddgs, tavily,unknown"}):
            self.assertEqual([p.name for p in S.get_providers()], ["ddgs", "tavily"])
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual([p.name for p in S.get_providers()], ["searxng", "ddgs"])

    def test_enrich_and_truncate(self):
        with mock.patch.object(S, "fetch_text", return_value="正" * 3000):
            out = S.web_search("q", providers=[Fake("a", rows=[ROW])])
        self.assertEqual(len(out[0]["content"]), 1500)

    def test_enrich_keeps_snippet_on_fail(self):
        with mock.patch.object(S, "fetch_text", return_value=""):
            out = S.web_search("q", providers=[Fake("a", rows=[ROW])])
        self.assertEqual(out[0]["content"], "摘要")


if __name__ == "__main__":
    unittest.main()