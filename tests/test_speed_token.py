"""Narrow offline regression; execute ONLY through tests/run_speed_round.py."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from app import search as S, researcher as R, graph as G, server
from app.search.session import SearchSession, unique_sources, current_session
from tests.test_search import Fake, ROW


def rows(n=8):
    return [dict(ROW, id=i+1, url=f"https://example.test/{i}", content="文"*1500, type="web") for i in range(n)]


class CacheTests(unittest.TestCase):
    def test_key_and_bypass(self):
        p, q, session = Fake("a", [ROW]), Fake("b", [ROW]), SearchSession()
        call = lambda **kw: S.web_search("q", providers=[p], fetch=False, session=session, **kw)
        call(); call(); self.assertEqual(p.calls, 1)
        call(bypass_cache=True); self.assertEqual(p.calls, 2)
        call(); self.assertEqual(p.calls, 3)  # bypass invalidates and does not populate
        S.web_search("other", providers=[p], fetch=False, session=session)
        S.web_search("q", max_results=2, providers=[p], fetch=False, session=session)
        S.web_search("q", providers=[q, p], fetch=False, session=session)
        with mock.patch.object(S, 'FETCH_TOP', 0):
            S.web_search("q", providers=[p], fetch=False, session=session)
        self.assertEqual((p.calls, q.calls), (6, 1))

    def test_endpoint_and_fetch_key(self):
        session = SearchSession()
        with mock.patch.object(S.SearXNGProvider, "search", return_value=[ROW]) as search, mock.patch.object(S, 'fetch_text', return_value=''):
            for endpoint, fetch in [('http://a', False), ('http://a', False), ('http://b', False), ('http://b', True)]:
                S.web_search('q', providers=[S.SearXNGProvider(endpoint)], fetch=fetch, session=session)
        self.assertEqual(search.call_count, 3)

    def test_ttl_capacity_and_copy(self):
        now = [0]
        session, p = SearchSession(capacity=1, ttl=2, clock=lambda: now[0]), Fake('a', [ROW])
        def call(query): return S.web_search(query, providers=[p], fetch=False, session=session)
        a = call('q'); a[0]['content'] = 'changed'
        self.assertEqual(call('q')[0]['content'], ROW['content'])
        now[0] = 2; call('q'); self.assertEqual(p.calls, 2)
        call('x'); self.assertEqual(len(session.entries), 1)
        call('q'); self.assertEqual(p.calls, 4)
        disabled = SearchSession(capacity=0)
        disabled.search('k', lambda: [ROW]); self.assertFalse(disabled.entries)

    def test_no_failure_or_empty_cache(self):
        for p in [Fake('a', [], None), Fake('a', err=RuntimeError('down'))]:
            session = SearchSession()
            for _ in range(2):
                try: S.web_search('q', providers=[p], fetch=False, session=session)
                except RuntimeError: pass
            self.assertEqual(p.calls, 2); self.assertFalse(session.entries)

    def test_concurrency_and_scope(self):
        p, session = Fake('a', [ROW]), SearchSession()
        with ThreadPoolExecutor(4) as pool:
            outputs = list(pool.map(lambda _: S.web_search('q', providers=[p], fetch=False, session=session), range(8)))
        self.assertEqual(p.calls, 1); self.assertEqual(len(outputs), 8)
        with SearchSession() as scoped:
            S.web_search('q', providers=[p], fetch=False)
            S.web_search('q', providers=[p], fetch=False)
        self.assertFalse(scoped.entries); self.assertIsNone(current_session.get())
        with SearchSession(): S.web_search('q', providers=[p], fetch=False)
        self.assertEqual(p.calls, 3)
        with self.assertRaises(ValueError):
            with SearchSession(): raise ValueError('failure')
        self.assertIsNone(current_session.get())


class BudgetTests(unittest.TestCase):
    def test_fetch_dedupe_and_zero(self):
        data = rows(4) + [dict(rows(1)[0], url='https://example.test/0#fragment')]
        with mock.patch.object(S, 'fetch_text', return_value='正文'*1000) as fetch, mock.patch.object(S, 'FETCH_TOP', 2):
            out = S.web_search('q', providers=[Fake('a', data)])
        self.assertEqual(len(out), 4); self.assertEqual(fetch.call_count, 2)
        self.assertTrue(all(len(r['content']) <= 1500 for r in out))
        with mock.patch.object(S, 'FETCH_TOP', 0), mock.patch.object(S, 'fetch_text') as fetch:
            S.web_search('q', providers=[Fake('a', data)])
        fetch.assert_not_called()

    def test_context_and_local_pages(self):
        local = [dict(ROW, id=9+i, type='local', url=f'/api/kb/docs/abc/file#page={i+1}', page=i+1, file='manual', content='本'*1500) for i in range(2)]
        context, kept = R.build_context(rows() + local)
        self.assertLessEqual(len(context), R.CONTEXT_LIMIT)
        self.assertEqual([s['page'] for s in kept[:2]], [1, 2])
        self.assertEqual(len(unique_sources(local)), 2)
        self.assertEqual(len({s['id'] for s in kept}), len(kept))
        with mock.patch.object(R, 'CONTEXT_LIMIT', 1): self.assertEqual(R.build_context(local), ('', []))

    def test_relevance_budget(self):
        llm = mock.Mock()
        llm.with_structured_output.return_value.invoke.return_value = R.Relevance(relevant_ids=[1, 2, 3])
        with mock.patch.object(R, 'get_llm', return_value=llm), mock.patch.object(R, 'CONTEXT_LIMIT', 500):
            selected = R.filter_sources('q', [dict(r, title='题'*10000) for r in rows()])
        self.assertEqual(len(selected), 1)
        self.assertLessEqual(len(llm.with_structured_output.return_value.invoke.call_args.args[0][1].content.split('资料：\n')[1]), 500)

    def test_rewrite_ids_and_kb_fallback(self):
        local = dict(ROW, type='local', url='/api/kb/docs/abc/file#page=1', page=1, file='manual')
        seen = []
        def filtering(q, sources):
            seen.append([s['id'] for s in sources]); return sources
        llm = mock.Mock()
        llm.with_structured_output.return_value.invoke.return_value = R.ResearchNote(question='技术问题', findings=[R.Finding(point='事实', source_ids=[1])])
        extra = [dict(ROW, url='https://b.test'), dict(ROW, url='https://b.test#x'), dict(ROW, url='https://c.test')]
        with mock.patch.object(R, 'search_local', return_value=[local]), mock.patch.object(R, 'search_web', side_effect=[[], extra, extra]), mock.patch.object(R, 'rewrite_queries', return_value=['a', 'b']), mock.patch.object(R, 'filter_sources', side_effect=filtering), mock.patch.object(R, 'get_llm', return_value=llm):
            note, sources = R.research('技术问题')
        self.assertEqual(seen, [[1], [2, 3]])
        self.assertEqual(len(sources), 3); self.assertEqual(note.findings[0].source_ids, [1])
        self.assertEqual(sources[0]['page'], 1)
        with mock.patch.object(R, 'search', side_effect=RuntimeError('offline')):
            self.assertEqual(R.search_web('q', 5), [])

    def test_mock_evidence(self):
        data = rows()
        before = '\n\n'.join(f"[{s['id']}]（网页）{s['title']}\n{s['content']}" for s in data)
        after, _ = R.build_context(data)
        p, session = Fake('a', data), SearchSession()
        with mock.patch.object(S, 'fetch_text', return_value='正文'*750), mock.patch.object(S, 'FETCH_TOP', 2):
            S.web_search('fixture', providers=[p], session=session)
            cold = dict(session.metrics)
            S.web_search('fixture', providers=[p], session=session)
        evidence = dict(mode='deterministic_mock', before_context_chars=len(before), after_context_chars=len(after),
                        old_fetch_top=3, new_fetch_top=2, cold=cold, warm=dict(session.metrics),
                        real_research_requests=0, real_llm_calls=0, tokens=None)
        print('FIXTURE_EVIDENCE ' + json.dumps(evidence, sort_keys=True))
        self.assertLess(len(after), len(before)); self.assertEqual(p.calls, 1)


class ContractTests(unittest.TestCase):
    def test_writer_reference_remap(self):
        note = dict(question='q', findings=[dict(point='fact', source_ids=[9])], sources=[dict(ROW, id=9)])
        llm = mock.Mock(); llm.invoke.return_value.content = 'report [1]'
        with mock.patch.object(G, 'get_llm', return_value=llm):
            self.assertEqual(G.writer_node(dict(question='q', notes=[note, note]))['report'], 'report [1]')
        prompt = llm.invoke.call_args.args[0][1].content
        self.assertEqual(prompt.count('https://a.com'), 1)
        self.assertEqual(prompt.count('fact [1]'), 2)

    def test_sse_and_resume_mock_contract(self):
        fake = mock.Mock()
        fake.stream.return_value = iter([{'researcher': {'notes': [dict(question='q', findings=[], sources=[dict(ROW, type='local')])]}}, {'writer': {'report': 'r'}}])
        fake.get_state.return_value = SimpleNamespace(values={'report': 'r'}, next=('researcher',))
        with mock.patch.object(server, 'graph', fake):
            events = list(server._event_stream('id', None, ['resume']))
            self.assertEqual([e['event'] for e in events], ['start', 'node', 'node', 'done'])
            self.assertEqual(json.loads(events[1]['data']), dict(node='researcher', question='q', findings=0, sources=1, local=1))
            self.assertIsNone(fake.stream.call_args.args[0])
            self.assertEqual(fake.stream.call_args.kwargs['stream_mode'], 'updates')
            response = server.resume_research('id')
            self.assertEqual(response.media_type, 'text/event-stream')
