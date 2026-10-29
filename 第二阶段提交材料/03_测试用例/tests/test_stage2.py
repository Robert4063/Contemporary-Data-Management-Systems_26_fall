"""Requirement-change acceptance tests run against a real isolated MongoDB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from bson import ObjectId

from .conftest import login
from .test_system import create, publish, q, submit


def saved(client, headers, kind='number', **values):
    body = {k: v for k, v in q('template', kind, **values).items() if k not in ('id', 'rules')}
    response = client.post('/api/questions', headers=headers, json=body)
    assert response.status_code == 201, response.text
    version=response.json()
    collected=client.post('/api/usual-questions',headers=headers,json={
        'question_id':version['question_id'],'question_version':version['question_version']})
    assert collected.status_code==201,collected.text
    return version


def reused(client, headers, version, local_id='q1'):
    survey = create(client, headers, [])
    response = client.post(f"/api/surveys/{survey['_id']}/reuse-question", headers=headers,
        json={'version_id': version['_id'], 'local_id': local_id, 'revision': survey['revision']})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.case(title='S01 保存与多卷复用', input='保存数字题→两个问卷分别选用→发布作答', expected='一个版本被两个本地ID引用；旧增题、跳转及统计协议可用')
def test_stage2_save_reuse(client, owner):
    version = saved(client, owner, title='你的年龄', validation={'minimum': 0, 'maximum': 120, 'integer_only': True})
    a = reused(client, owner, version, 'age_a')
    b = reused(client, owner, version, 'age_b')
    assert a['questions'][0]['source_version_id'] == b['questions'][0]['source_version_id'] == version['_id']
    for survey, local_id in [(a, 'age_a'), (b, 'age_b')]:
        publish(client, owner, survey)
        assert submit(client, owner, survey, {local_id: 20}).status_code == 201
        assert client.get(f"/api/surveys/{survey['_id']}/statistics/{local_id}", headers=owner).json()['average'] == 20
    assert client.get('/api/questions', headers=owner).json()['total'] == 1
    assert client.get('/api/questions').status_code == 401
    assert client.post('/api/questions', headers=owner, json={'title': 'bad', 'type': 'single'}).status_code == 422


@pytest.mark.case(title='S01 选题冲突与非法来源', input='重复本地ID、过期revision、他人题目、伪造快照', expected='422/409/404；不改变问卷')
def test_stage2_reuse_guards(client, owner):
    version = saved(client, owner)
    s = reused(client, owner, version)
    path = f"/api/surveys/{s['_id']}/reuse-question"
    assert client.post(path, headers=owner, json={'version_id': version['_id'], 'local_id': 'q1', 'revision': s['revision']}).status_code == 422
    assert client.post(path, headers=owner, json={'version_id': version['_id'], 'local_id': 'q2', 'revision': 1}).status_code == 409
    other = login(client, 'outsider')
    assert client.get('/api/questions/'+version['_id'], headers=other).status_code == 404
    forged = deepcopy(s['questions'][0]); forged['title'] = '伪造'
    assert client.post('/api/surveys', headers=owner, json={'title': 'bad', 'deadline': s['deadline'], 'questions': [forged]}).status_code == 422
    assert client.get('/api/questions?limit=0', headers=owner).status_code == 422


@pytest.mark.case(title='S02 共享及撤销', input='共享给学生→师生复用→第三者访问→撤销→保存旧快照', expected='师生可用，第三者404；撤销禁止新引用但不破坏已存问卷')
def test_stage2_share_revoke(client, owner):
    student = login(client, 'student')
    stranger = login(client, 'stranger')
    v = saved(client, owner)
    base = '/api/questions/'+v['_id']
    assert client.post(base+'/shares', headers=owner, json={'username': 'student'}).status_code == 200
    assert client.get('/api/questions', headers=student).json()['total'] == 1
    s = reused(client, student, v)
    reused(client, owner, v)
    assert client.get(base, headers=stranger).status_code == 404
    assert client.post(base+'/shares', headers=student, json={'username': 'stranger'}).status_code == 404
    assert client.get(base+'/shares', headers=owner).json()['items'][0]['username'] == 'student'
    assert client.delete(base+'/shares/student', headers=owner).status_code == 200
    assert client.get(base, headers=student).status_code == 404
    body = {k: s[k] for k in ('title', 'description', 'deadline', 'allow_anonymous', 'allow_multiple', 'questions', 'revision')}
    assert client.put('/api/surveys/'+s['_id'], headers=student, json=body).status_code == 200
    publish(client, student, s)
    assert submit(client, student, s, {'q1': 18}).status_code == 201
    fresh = create(client, student, [])
    assert client.post('/api/surveys/'+fresh['_id']+'/reuse-question', headers=student,
        json={'version_id': v['_id'], 'local_id': 'q1', 'revision': fresh['revision']}).status_code == 404


@pytest.mark.case(title='S03 草稿可编辑与发布保护', input='草稿编辑来源题→生成新题版本→发布后PUT/增题/直接发布→旧答卷核对', expected='草稿编辑成功且题库原版不变；发布后禁止修改，原答卷不变')
def test_stage2_snapshot_freeze(client, owner):
    v = saved(client, owner, title='原题')
    s = reused(client, owner, v)
    base = '/api/surveys/'+s['_id']
    body = {k: s[k] for k in ('title', 'description', 'deadline', 'allow_anonymous', 'allow_multiple', 'questions', 'revision')}
    body['questions'][0]['title'] = '改过的题'
    changed = client.put(base, headers=owner, json=body)
    assert changed.status_code == 200, changed.text
    s = changed.json()
    body = {k: deepcopy(s[k]) for k in ('title', 'description', 'deadline', 'allow_anonymous', 'allow_multiple', 'questions', 'revision')}
    assert s['questions'][0]['title']=='改过的题'
    assert client.get('/api/questions/'+v['_id'], headers=owner).json()['content']['title']=='原题'
    publish(client, owner, s)
    assert submit(client, owner, s, {'q1': 20}).status_code == 201
    before = list(client.app.state.db.responses.find())
    body['questions'][0]['title'] = '发布后再次修改'
    assert client.put(base, headers=owner, json=body).status_code == 409
    assert client.post(base+'/questions', headers=owner, json=q('q2')).status_code == 409
    assert client.post(base+'/publish-direct', headers=owner, json=body).status_code == 409
    assert client.get(base, headers=owner).json()['questions'] == s['questions']
    assert list(client.app.state.db.responses.find()) == before


@pytest.mark.case(title='S04 S05 版本分支、恢复与共存', input='v1→v2、v1→v3分支→恢复v1→各卷引用', expected='来源关系正确，旧题不变，恢复为新版本，不同版本可同时发布使用')
def test_stage2_versions_restore(client, owner):
    v1 = saved(client, owner, title='年龄v1')
    a = reused(client, owner, v1); publish(client, owner, a)
    content = {**v1['content'], 'title': '年龄v2'}
    v2 = client.post('/api/questions/'+v1['_id']+'/versions', headers=owner, json=content).json()
    v3 = client.post('/api/questions/'+v1['_id']+'/versions', headers=owner, json={**content, 'title': '分支v3'}).json()
    assert v2['version'] == 2 and v3['version'] == 3
    assert v3['parent_id'] == v2['parent_id'] == v1['_id']
    r = client.post('/api/questions/'+v1['_id']+'/restore', headers=owner)
    assert r.status_code == 201
    restored = r.json()
    assert restored['content'] == v1['content'] and restored['version'] == 4
    assert restored['restored_from'] == v1['_id']
    history = client.get('/api/questions/'+v2['_id']+'/history', headers=owner).json()['items']
    assert len(history) == 4
    b = reused(client, owner, v2); publish(client, owner, b)
    assert client.get('/api/surveys/'+a['_id'], headers=owner).json()['questions'][0]['title'] == '年龄v1'
    assert b['questions'][0]['title'] == '年龄v2'
    assert client.put('/api/questions/'+v1['_id'], headers=owner, json=content).status_code == 405


@pytest.mark.case(title='S05 并发版本与共享派生', input='共享接收者派生；作者并发创建6版', expected='派生独立归属、保留来源；作者版本号不重复、原内容不变')
def test_stage2_concurrent_versions(client, owner):
    v = saved(client, owner)
    other = login(client, 'collaborator')
    client.post('/api/questions/'+v['_id']+'/shares', headers=owner, json={'username': 'collaborator'})
    fork = client.post('/api/questions/'+v['_id']+'/versions', headers=other, json={**v['content'], 'title': '学生改题'})
    assert fork.status_code == 201
    fork = fork.json()
    assert fork['root_id'] != v['root_id'] and fork['owner_id'] != v['owner_id'] and fork['parent_id'] == v['_id']
    assert client.get('/api/questions/'+fork['_id'], headers=owner).status_code == 404
    def make(i):
        return client.post('/api/questions/'+v['_id']+'/versions', headers=owner, json={**v['content'], 'title': str(i)})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(make, range(6)))
    assert all(r.status_code == 201 for r in results)
    assert {r.json()['version'] for r in results} == set(range(2, 8))


@pytest.mark.case(title='S06 引用查询及隔离', input='作者两卷、接收者一卷使用相同版本', expected='作者能看总引用数，明细仅包含自己的问卷，不暴露他人标题')
def test_stage2_usages(client, owner):
    v = saved(client, owner)
    a = reused(client, owner, v); b = reused(client, owner, v)
    other = login(client, 'usageother')
    client.post('/api/questions/'+v['_id']+'/shares', headers=owner, json={'username': 'usageother'})
    reused(client, other, v)
    data = client.get('/api/questions/'+v['_id']+'/usages', headers=owner).json()
    assert data['total_survey_count'] == 3 and data['visible_survey_count'] == 2
    assert {s['survey_id'] for s in data['items']} == {a['_id'], b['_id']}
    assert client.get('/api/questions/'+v['_id']+'/usages', headers=other).json()['visible_survey_count'] == 1






@pytest.mark.case(title='S08 数字题跨卷分布及版本隔离', input='两卷本地ID不同，各填10、20；新版本填100；他人卷填80', expected='旧版两卷均值15、分布10/20；新版独立；他人答卷不泄露')
def test_stage2_cross_statistics(client, owner):
    v = saved(client, owner)
    path = '/api/questions/'+v['_id']
    assert client.get(path+'/statistics', headers=owner).json()['average'] is None
    for local, value in [('first_age', 10), ('second_age', 20)]:
        s = reused(client, owner, v, local); publish(client, owner, s)
        assert submit(client, owner, s, {local: value}).status_code == 201
    v2 = client.post(path+'/versions', headers=owner, json={**v['content'], 'title': '新年龄'}).json()
    s = reused(client, owner, v2); publish(client, owner, s)
    assert submit(client, owner, s, {'q1': 100}).status_code == 201
    other = login(client, 'statsother')
    client.post(path+'/shares', headers=owner, json={'username': 'statsother'})
    s = reused(client, other, v); publish(client, other, s)
    assert submit(client, other, s, {'q1': 80}).status_code == 201
    data = client.get(path+'/statistics', headers=owner).json()
    assert data['answer_count'] == data['response_count'] == data['survey_count'] == 2
    assert data['average'] == 15 and data['contents'] == [10, 20]
    assert data['distribution'] == [{'value': 10, 'count': 1}, {'value': 20, 'count': 1}]
    assert client.get(path+'/statistics', headers=other).json()['average'] == 80


@pytest.mark.parametrize('kind,answer', [('single', 'a'), ('multiple', ['a', 'b']), ('text', '正常')])
@pytest.mark.case(title='S08 选择与文本跨卷统计', input='两卷复用同一版本并作答', expected='单选各选项次数、多选次数、文本内容与回答数正确')
def test_stage2_cross_types(client, owner, kind, answer):
    v = saved(client, owner, kind)
    for local in ('a1', 'b2'):
        s = reused(client, owner, v, local); publish(client, owner, s)
        assert submit(client, owner, s, {local: answer}).status_code == 201
    data = client.get('/api/questions/'+v['_id']+'/statistics', headers=owner).json()
    assert data['answer_count'] == 2
    if kind == 'text':
        assert data['contents'] == ['正常', '正常']
    else:
        assert data['options'][0]['count'] == 2
        assert data['options'][1]['count'] == (2 if kind == 'multiple' else 0)


@pytest.mark.case(title='S08 同卷重复与可选空答', input='同一版本在同卷出现两次，答0、10；另一卷不答选答题', expected='回答数2、答卷数1、均值5；0保留，空答不计入')
def test_stage2_repeated_and_empty(client, owner):
    v = saved(client, owner, required=False)
    s = reused(client, owner, v, 'first')
    s = client.post('/api/surveys/'+s['_id']+'/reuse-question', headers=owner,
        json={'version_id': v['_id'], 'local_id': 'second', 'revision': s['revision']}).json()
    publish(client, owner, s)
    assert submit(client, owner, s, {'first': 0, 'second': 10}).status_code == 201
    empty = reused(client, owner, v); publish(client, owner, empty)
    assert submit(client, owner, empty, {'q1': None}).status_code == 201
    data = client.get('/api/questions/'+v['_id']+'/statistics', headers=owner).json()
    assert data['answer_count'] == 2 and data['response_count'] == 1 and data['average'] == 5


@pytest.mark.case(title='旧数据不迁移', input='直接放入无source_version_id的第一阶段题及答卷→初始化→统计', expected='旧结构原样保留，旧统计正常，新统计不错误关联相同题号')
def test_stage2_legacy_compatibility(client, owner):
    from app.db import initialize
    s = create(client, owner, [q('q1', 'number')]); publish(client, owner, s)
    assert submit(client, owner, s, {'q1': 12}).status_code == 201
    db = client.app.state.db
    db.surveys.update_one({'_id': ObjectId(s['_id'])}, {'$unset': {'questions.0.source_version_id': ''}})
    before = db.surveys.find_one({'_id': ObjectId(s['_id'])})
    initialize(db)
    assert db.surveys.find_one({'_id': before['_id']}) == before
    assert client.get('/api/surveys/'+s['_id']+'/statistics', headers=owner).json()['questions'][0]['average'] == 12
    v = saved(client, owner)
    assert client.get('/api/questions/'+v['_id']+'/statistics', headers=owner).json()['answer_count'] == 0


@pytest.mark.case(title='S01 S03 可复用题的本地跳转及来源标准化', input='同一单选版本两卷分别设置END或后续题；来源ID大写', expected='来源标准化为小写，规则仅作用本卷，其他卷和通用版本不变')
def test_stage2_local_routing(client, owner):
    v = saved(client, owner, 'single')
    s = reused(client, owner, v, 'start')
    original = deepcopy(s['questions'][0])
    routed = {**original, 'source_version_id': v['_id'].upper(), 'jump_enabled': True,
        'rules': [{'op': 'eq', 'value': 'a', 'target': 'END'}]}
    a = create(client, owner, [routed, q('tail', 'text')])
    b = create(client, owner, [original, q('tail', 'text')])
    assert a['questions'][0]['source_version_id'] == v['_id']
    publish(client, owner, a); publish(client, owner, b)
    assert submit(client, owner, a, {'start': 'a'}).status_code == 201
    assert submit(client, owner, b, {'start': 'a'}).status_code == 422
    assert submit(client, owner, b, {'start': 'a', 'tail': '完整回答'}).status_code == 201
    assert 'rules' not in client.get('/api/questions/'+v['_id'], headers=owner).json()['content']
