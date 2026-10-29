import json
import hashlib
import os
import shutil
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import Settings
from app.main import create_app

TEST_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = TEST_ROOT.parent
ROOT = PACKAGE / '01_代码'
OUTPUT = TEST_ROOT / '运行结果'
OUTPUT.mkdir(exist_ok=True)
RESULTS = []
ENVIRONMENT = {}
WARNINGS = []


@pytest.fixture(scope='session')
def mongo_uri():
    """Use a dedicated local mongod or an explicitly supplied test URI, never the app DB."""
    external = os.getenv('TEST_MONGO_URI')
    if external:
        with MongoClient(external, serverSelectionTimeoutMS=3000) as client:
            client.admin.command('ping')
            ENVIRONMENT['mongodb_version'] = client.server_info()['version']
        yield external
        return
    executable = os.getenv('MONGOD_BIN') or shutil.which('mongod')
    if not executable:
        pytest.fail('真实 MongoDB 测试需要 mongod；请设置 MONGOD_BIN 或 TEST_MONGO_URI')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    data = OUTPUT / 'runtime' / 'test-mongo' / uuid.uuid4().hex
    data.mkdir(parents=True)
    process = subprocess.Popen([executable, '--bind_ip', '127.0.0.1', '--port', str(port),
        '--dbpath', str(data), '--logpath', str(data / 'mongod.log')],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    uri = f'mongodb://127.0.0.1:{port}'
    probe = MongoClient(uri, serverSelectionTimeoutMS=250, connectTimeoutMS=250)
    try:
        for _ in range(80):
            try:
                probe.admin.command('ping')
                break
            except PyMongoError:
                if process.poll() is not None:
                    pytest.fail('mongod 启动失败：' + (data / 'mongod.log').read_text(encoding='utf-8', errors='replace')[-4000:])
                time.sleep(.1)
        else:
            pytest.fail('测试 MongoDB 未能在期限内启动')
        ENVIRONMENT['mongodb_version'] = probe.server_info()['version']
        yield uri
    finally:
        probe.close()
        process.terminate()
        process.wait(timeout=20)
        # Keep the server log as evidence; no recursive deletion of external paths.


@pytest.fixture
def client(mongo_uri, request):
    name = 'test_questionnaire_' + uuid.uuid4().hex
    settings = Settings(mongo_uri=mongo_uri, mongo_db=name, app_secret='test-only-stable-secret-at-least-32-characters')
    with TestClient(create_app(settings)) as c:
        request.node.api_trace = []

        def trace(response):
            response.read()
            try:
                body = json.loads(response.request.content, parse_constant=lambda value: {'nonstandard_json_number': value}) if response.request.content else None
            except (ValueError, UnicodeError):
                body = '<non-json>'
            if isinstance(body, dict) and 'password' in body:
                body = {**body, 'password': '<redacted>'}
            try:
                output = response.json()
                if isinstance(output, dict) and 'access_token' in output:
                    output = {**output, 'access_token': '<redacted>'}
            except ValueError:
                output = '<HTML/static content>'
            request.node.api_trace.append({'method': response.request.method, 'path': response.request.url.path,
                'input': body, 'status': response.status_code, 'actual_output': output})

        c.event_hooks['response'].append(trace)
        yield c
    assert name.startswith('test_questionnaire_')
    with MongoClient(mongo_uri) as cleanup:
        cleanup.drop_database(name)


def login(client, username='owner'):
    body = {'username': username, 'password': 'test-password-123'}
    assert client.post('/api/auth/register', json=body).status_code == 201
    response = client.post('/api/auth/login', json=body)
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


@pytest.fixture
def owner(client):
    return login(client)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == 'teardown' and report.failed:
        row=next((row for row in RESULTS if row['test']==item.nodeid),None)
        if row:
            row['result']='failed'
            row['failure']=(row['failure'] or '')+'\nTeardown: '+str(report.longrepr)
            return
    if report.when == 'call' or (report.when == 'setup' and (report.failed or report.skipped)) or (report.when == 'teardown' and report.failed):
        marker = item.get_closest_marker('case')
        RESULTS.append({'test': item.nodeid, 'description': marker.kwargs if marker else {},
            'result': report.outcome, 'duration_seconds': round(report.duration, 4),
            'actual_api_trace': getattr(item, 'api_trace', []),
            'browser_summary': getattr(item, 'browser_summary', None),
            'failure': str(report.longrepr) if report.failed else None})


def pytest_warning_recorded(warning_message, when, nodeid, location):
    WARNINGS.append({'category':warning_message.category.__name__,'message':str(warning_message.message)})


def pytest_sessionfinish(session, exitstatus):
    target = OUTPUT / 'test-results.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'executed_at_utc': datetime.now(timezone.utc).isoformat(),
        'exit_code': int(exitstatus), 'database': 'real MongoDB, isolated test databases',
        'scope': os.getenv('SURVEY_TEST_SCOPE','unclassified'),
        'requested_args': list(session.config.invocation_params.args),
        'collected_count': session.testscollected, 'warnings': WARNINGS,
        'source_sha256': {p.relative_to(PACKAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('01_代码/backend','01_代码/frontend','01_代码/scripts','03_测试用例/tests') for p in sorted((PACKAGE/folder).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.js','.css','.html')},
        'environment': ENVIRONMENT,
        'results': RESULTS}, ensure_ascii=False, indent=2), encoding='utf-8')
