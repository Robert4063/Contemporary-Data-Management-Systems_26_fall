"""Course-mandated original flows combined with reusable stage-two questions."""
from copy import deepcopy
from uuid import uuid4

import pytest
from bson import ObjectId

from .conftest import login
from .test_stage2 import saved
from .test_system import create, publish, q, submit


@pytest.mark.case(title='课程组合一 共享题跳转校验匿名及幂等',
    input='作者共享年龄题→接收者组卷：单选a跳年龄、b跳文本→错误年龄-1拒绝→匿名提交年龄20→相同UUID重试',
    expected='路径为gate/age；非法值422；首次201重试200且仅一份；匿名无respondent_id；接收者跨卷均值20、作者无答卷')
def test_course_shared_jump_validation_submission(client, owner):
    recipient = login(client, 'course_receiver')
    version = saved(client, owner, title='年龄', validation={'minimum': 0, 'maximum': 120, 'integer_only': True})
    assert client.post('/api/questions/'+version['_id']+'/shares', headers=owner,
        json={'username': 'course_receiver'}).status_code == 200
    gate = q('gate', jump_enabled=True, rules=[{'op': 'eq', 'value': 'b', 'target': 'feedback'}])
    age = {'id': 'age', 'source_version_id': version['_id'], **version['content'], 'jump_enabled': True,
        'rules': [{'op': 'gte', 'value': 0, 'target': 'END'}]}
    s = create(client, recipient, [gate, age, q('feedback', 'text')], allow_anonymous=True)
    publish(client, recipient, s)
    base = '/api/public/surveys/'+s['_id']
    step = client.post(base+'/next', headers=recipient, json={'answers': {'gate': 'a'}})
    assert step.status_code == 200 and step.json()['next_question_id'] == 'age'
    assert submit(client, recipient, s, {'gate': 'a', 'age': -1}, True).status_code == 422
    body = {'submission_id': str(uuid4()), 'anonymous': True, 'answers': {'gate': 'a', 'age': 20}}
    assert client.post(base+'/responses', headers=recipient, json=body).status_code == 201
    replay = client.post(base+'/responses', headers=recipient, json=body)
    assert replay.status_code == 200 and replay.json()['replayed']
    stored = list(client.app.state.db.responses.find({'survey_id': ObjectId(s['_id'])}))
    assert len(stored) == 1 and 'respondent_id' not in stored[0]
    assert stored[0]['path'] == ['gate', 'age']
    assert client.get('/api/questions/'+version['_id']+'/statistics', headers=recipient).json()['average'] == 20
    assert client.get('/api/questions/'+version['_id']+'/statistics', headers=owner).json()['answer_count'] == 0


@pytest.mark.case(title='课程组合二 恢复版本不重写旧问卷答卷',
    input='v1问卷回答10→基于v1创建v2→恢复v1为v3→新卷引用v3回答30→比较原卷和原答卷',
    expected='v1内容、快照、答卷均原样保留；v3标明来源v1；v1均值10、v3均值30；旧统计仍10')
def test_course_restore_preserves_historical_data(client, owner):
    from .test_stage2 import reused
    version = saved(client, owner, title='旧年龄')
    a = reused(client, owner, version); publish(client, owner, a)
    assert submit(client, owner, a, {'q1': 10}).status_code == 201
    db = client.app.state.db
    before_survey = deepcopy(db.surveys.find_one({'_id': ObjectId(a['_id'])}))
    before_response = deepcopy(db.responses.find_one({'survey_id': ObjectId(a['_id'])}))
    assert client.post('/api/questions/'+version['_id']+'/versions', headers=owner,
        json={**version['content'], 'title': '今年年龄'}).status_code == 201
    r = client.post('/api/questions/'+version['_id']+'/restore', headers=owner)
    assert r.status_code == 201
    restored = r.json()
    assert restored['version'] == 3 and restored['restored_from'] == version['_id']
    b = reused(client, owner, restored); publish(client, owner, b)
    assert submit(client, owner, b, {'q1': 30}).status_code == 201
    assert db.surveys.find_one({'_id': before_survey['_id']}) == before_survey
    assert db.responses.find_one({'_id': before_response['_id']}) == before_response
    for v, expected in [(version, 10), (restored, 30)]:
        assert client.get('/api/questions/'+v['_id']+'/statistics', headers=owner).json()['average'] == expected
    assert client.get('/api/surveys/'+a['_id']+'/statistics/q1', headers=owner).json()['average'] == 10


@pytest.mark.case(title='课程组合三 题库中的新旧版本可在同卷共存',
    input='数字v1与修改为单选的v2显式分别入库→同卷用不同本地ID引用→回答0和a',
    expected='同族不同题型版本均可用；来源保持独立；v1平均0、v2选项a计1；原整卷统计有两题')
def test_course_question_versions_coexist(client, owner):
    version = saved(client, owner)
    content = {k: v for k, v in q('template', 'single').items() if k not in ('id', 'rules')}
    r = client.post('/api/questions/'+version['_id']+'/versions', headers=owner, json=content)
    assert r.status_code == 201
    newer = r.json()
    client.post('/api/usual-questions',headers=owner,json={'question_id':newer['question_id'],'question_version':newer['question_version']})
    versions=[version,newer]
    questions = [{'id': 'local'+str(i), 'source_version_id': v['_id'], **v['content']} for i, v in enumerate(versions)]
    s = create(client, owner, questions); publish(client, owner, s)
    assert submit(client, owner, s, {'local0': 0, 'local1': 'a'}).status_code == 201
    assert client.get('/api/questions/'+version['_id']+'/statistics', headers=owner).json()['average'] == 0
    assert client.get('/api/questions/'+newer['_id']+'/statistics', headers=owner).json()['options'][0]['count'] == 1
    assert len(client.get('/api/surveys/'+s['_id']+'/statistics', headers=owner).json()['questions']) == 2
