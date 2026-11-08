"""One invocation = the single authorized offline test round. No live services or credentials."""
import os
import sys
import json
import socket
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
evidence = root / 'docs/evidence/speed-token-20261106'
started = datetime.now(timezone(timedelta(hours=8))).isoformat()
real_connect = sqlite3.connect
with tempfile.TemporaryDirectory(prefix='deep-research-offline-') as tmp, \
     mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')), \
     mock.patch('app.observability.enabled', return_value=False), \
     mock.patch('app.llm.get_llm', side_effect=AssertionError('real LLM forbidden')), \
     mock.patch('chromadb.PersistentClient'), \
     mock.patch('sqlite3.connect', side_effect=lambda *a, **kw: real_connect(str(Path(tmp)/'checkpoint.db'), **kw)):
    old = Path.cwd()
    os.chdir(tmp)
    try:
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            'tests.test_search', 'tests.test_observability.NoopTest', 'tests.test_speed_token'])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        os.chdir(old)
        server = sys.modules.get('app.server')
        if server is not None: server._conn.close()
summary = dict(started=started, ended=datetime.now(timezone(timedelta(hours=8))).isoformat(),
               tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
               skipped=len(result.skipped), successful=result.wasSuccessful(),
               real_research_requests=0, real_llm_calls=0, mode='offline_mock')
(evidence/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
sys.exit(0 if result.wasSuccessful() else 1)
