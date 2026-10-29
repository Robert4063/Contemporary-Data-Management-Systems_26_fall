from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from bson import ObjectId
import pytest

from .conftest import login
from .test_system import create, publish, q, submit
from .test_stage2 import saved, reused


def pair(version):
    return {k: version[k] for k in ('question_id', 'question_version')}


@pytest.mark.case(title='确认模型 六集合与题目快照', input='创建普通数字题问卷并保存修改', expected='仅6集合；questions按ID/版本存内容；草稿修改只增加revision，改题生成新题版本')
def test_confirmed_collections_and_local_history(client, owner):
    db = client.app.state.db
    assert set(db.list_collection_names()) == {'users','sessions','surveys','responses','questions','usual_questions'}
    s = create(client, owner, [q('q1', 'number', title='原题')])
    raw = db.surveys.find_one({'_id': ObjectId(s['_id'])})
    assert raw['survey_id'] == raw['_id'] and raw['survey_version'] == 1
    snap = raw['questions'][0]
    assert snap['content']['title'] == '原题' and snap['question_version'] == 1 and 'source_version_id' not in snap
    assert db.questions.find_one({'question_id': snap['question_id'], 'question_version': 1})['content'] == snap['content']
    body = {k:s[k] for k in ('title','description','allow_anonymous','allow_multiple','deadline','questions','revision')}
    body['questions'][0]['title'] = '新题'
    r = client.put('/api/surveys/'+s['_id'], headers=owner, json=body)
    assert r.status_code == 200, r.text
    assert r.json()['survey_version'] == 1 and r.json()['questions'][0]['question_version'] == 2
    assert db.questions.find_one({'question_id': snap['question_id'], 'question_version': 1})['content']['title'] == '原题'
    assert db.usual_questions.count_documents({}) == 0


@pytest.mark.case(title='确认模型 问卷真实版本和答卷关联', input='发布v1答10→新建v2改题并答20→查询各版', expected='逻辑ID相同、物理ID不同、版本1/2；答卷固定对应版本，各卷统计隔离')
def test_confirmed_survey_versions(client, owner):
    from app.db import initialize
    db = client.app.state.db
    # Reproduce both first-stage unique indexes before upgrading a real answer.
    db.responses.create_index([('survey_id', 1), ('respondent_key', 1)], unique=True,
        partialFilterExpression={'respondent_key': {'$type': 'string'}})
    db.responses.create_index([('survey_id', 1), ('submission_key', 1)], unique=True,
        partialFilterExpression={'submission_key': {'$type': 'string'}})
    a = create(client, owner, [q('age','number')]); publish(client, owner, a)
    assert submit(client, owner, a, {'age':10}).status_code == 201
    initialize(db)
    assert not any(index.get('unique') and list(index['key'])[0] == 'survey_id'
        for index in db.responses.list_indexes())
    old=deepcopy(db.surveys.find_one({'_id':ObjectId(a['_id'])}))
    r=client.post('/api/surveys/'+a['_id']+'/versions',headers=owner)
    assert r.status_code==201,r.text
    b=r.json()
    assert b['_id']!=a['_id'] and b['survey_id']==a['survey_id'] and b['survey_version']==2 and b['parent_version']==1
    assert db.surveys.find_one({'_id':old['_id']})==old
    publish(client,owner,b)
    assert submit(client,owner,b,{'age':20}).status_code==201
    for s,expected in [(a,10),(b,20)]:
        response=db.responses.find_one({'survey_document_id':ObjectId(s['_id'])})
        assert str(response['survey_id'])==a['survey_id'] and response['survey_version']==s['survey_version']
        result=client.get('/api/surveys/'+s['_id']+'/statistics',headers=owner).json()
        assert result['response_count']==1 and result['questions'][0]['average']==expected
    history=client.get('/api/surveys/'+b['_id']+'/versions',headers=owner).json()['items']
    assert [v['survey_version'] for v in history]==[1,2]
    other=login(client,'historyoutsider')
    assert client.get('/api/surveys/'+a['_id']+'/versions',headers=other).status_code==404


@pytest.mark.case(title='确认模型 精确版本共享', input='转发v1后作者创建v2；学生查询v2并转发v1', expected='学生仅可见v1，v2为404；学生转发被拒绝且第三人未获授权')
def test_confirmed_exact_shares(client, owner):
    student=login(client,'exactstudent');login(client,'exactthird')
    a=saved(client,owner)
    assert client.post('/api/questions/forward',headers=owner,json={'username':'exactstudent','items':[pair(a)]}).status_code==200
    b=client.post('/api/questions/'+a['_id']+'/versions',headers=owner,json={**a['content'],'title':'v2'}).json()
    assert client.get('/api/questions/'+a['_id'],headers=student).status_code==200
    assert client.get('/api/questions/'+b['_id'],headers=student).status_code==404
    assert client.post('/api/questions/forward',headers=student,json={'username':'exactthird','items':[pair(a)]}).status_code==403
    rows=client.app.state.db.usual_questions.find_one({'question_id':ObjectId(a['question_id']),'question_version':1})
    assert set(rows)=={'_id','owner_id','question_id','question_version','revision','created_at','updated_at','in_library'}
    assert 'content' not in rows


@pytest.mark.case(title='确认模型 批量及部分题目转发',input='两题批量转发并重试；部分选题转发其他账号',expected='只授予选定版本，重试不重复授权，不自动收录或建立命名题库')
def test_confirmed_forward_library_selection(client,owner):
    student=login(client,'bankrecipient');other=login(client,'partialrecipient')
    a=saved(client,owner,title='年龄');b=saved(client,owner,'text',title='说明')
    body={'username':'bankrecipient','items':[pair(a),pair(b)]}
    for _ in range(2):
        r=client.post('/api/questions/forward',headers=owner,json=body)
        assert r.status_code==200 and r.json()['forwarded_count']==2
    assert client.get('/api/questions',headers=student).json()['total']==2
    assert client.get('/api/usual-questions',headers=student).json()['total']==0
    assert client.post('/api/questions/forward',headers=owner,json={'username':'partialrecipient','items':[pair(a)]}).status_code==200
    assert client.get('/api/questions/'+b['_id'],headers=other).status_code==404


@pytest.mark.parametrize('bad', ['duplicate','unknown','mixed'])
@pytest.mark.case(title='确认模型 转发失败不预先授权',input='重复版本、不存在用户名、包含非作者题',expected='422/404/403，目标无任何新增授权')
def test_confirmed_forward_rejects(client,owner,bad):
    recipient=login(client,'rejectrecipient');foreign=login(client,'foreignauthor')
    a=saved(client,owner);b=saved(client,foreign)
    client.post('/api/questions/'+b['_id']+'/shares',headers=foreign,json={'username':'owner'})
    path='/api/questions/forward';body={'username':'rejectrecipient','items':[pair(a)]}
    if bad=='duplicate':body['items']*=2; expected=422
    elif bad=='unknown':body['username']='missinguser';expected=404
    elif bad=='mixed':body['items'].append(pair(b));expected=403
    r=client.post(path,headers=owner,json=body)
    assert r.status_code==expected,r.text
    assert client.get('/api/questions',headers=recipient).json()['total']==0


@pytest.mark.case(title='确认模型 双字段选题与版本索引',input='按question_id/question_version选入草稿，创建重复版本',expected='选题成功；同一题目和问卷版本组合不能重复')
def test_confirmed_pair_reuse(client,owner):
    from pymongo.errors import DuplicateKeyError
    v=saved(client,owner);s=create(client,owner,[])
    r=client.post('/api/surveys/'+s['_id']+'/reuse-question',headers=owner,json={**pair(v),'local_id':'age','revision':1})
    assert r.status_code==201,r.text
    doc=client.app.state.db.questions.find_one({'_id':ObjectId(v['_id'])});doc.pop('_id')
    with pytest.raises(DuplicateKeyError):client.app.state.db.questions.insert_one(doc)
    raw=client.app.state.db.surveys.find_one({'_id':ObjectId(s['_id'])});raw.pop('_id')
    with pytest.raises(DuplicateKeyError):client.app.state.db.surveys.insert_one(raw)


@pytest.mark.case(title='确认模型 第一阶段数据升级',input='插入第一阶段问卷及答卷→初始化两次',expected='旧链接和答案保留；补齐真实问卷/题目版本，第二次不改写已升级数据')
def test_confirmed_first_stage_upgrade(client,owner):
    from datetime import timedelta
    from app.db import initialize
    from app.models import Question
    from app.security import now
    db=client.app.state.db; identity=ObjectId()
    question=Question.model_validate(q('legacy','number')).model_dump(exclude={'question_id','question_version','source_version_id'})
    owner_id=ObjectId(client.get('/api/auth/me',headers=owner).json()['id'])
    legacy={'_id':identity,'owner_id':owner_id,'title':'旧卷','description':'','status':'published',
        'questions':[question],'deadline':now()+timedelta(days=7),'allow_anonymous':False,'allow_multiple':False,
        'revision':4,'schema_version':1,'created_at':now(),'updated_at':now(),'admissions':1}
    db.surveys.insert_one(legacy,bypass_document_validation=True)
    response={'survey_id':identity,'survey_revision':4,'schema_version':1,'answers':{'legacy':17},
        'path':['legacy'],'anonymous':False,'submitted_at':now()}
    db.responses.insert_one(response,bypass_document_validation=True)
    initialize(db)
    after=db.surveys.find_one({'_id':identity})
    assert after['survey_id']==identity and after['survey_version']==1 and after['questions'][0]['content']['title']=='legacy'
    stored=db.responses.find_one({'_id':response['_id']})
    assert stored['survey_document_id']==identity and stored['survey_version']==1 and stored['answers']==response['answers']
    assert client.get('/api/public/surveys/'+str(identity),headers=owner).status_code==200
    assert client.get('/api/surveys/'+str(identity)+'/statistics/legacy',headers=owner).json()['average']==17
    initialize(db)
    assert db.surveys.find_one({'_id':identity})==after and db.responses.find_one({'_id':response['_id']})==stored


@pytest.mark.case(title='确认模型 旧共享数据升级不恢复已撤销权限',input='导入原实验题目族和版本→升级→撤销→再次初始化',expected='保留历史记录，授权按版本转换；撤销后再次启动不会重新授权')
def test_confirmed_previous_model_upgrade(client,owner):
    from app.db import initialize
    from app.security import now
    student=login(client,'migratestudent')
    user_id=ObjectId(client.get('/api/auth/me',headers=owner).json()['id'])
    student_id=ObjectId(client.get('/api/auth/me',headers=student).json()['id'])
    db=client.app.state.db;root=ObjectId();vid=ObjectId()
    db.question_families.insert_one({'_id':root,'owner_id':user_id,'shared_with':[student_id]})
    db.question_versions.insert_one({'_id':vid,'root_id':root,'owner_id':user_id,'version':1,'parent_id':None,
        'content':{'title':'迁移题','type':'number','required':True,'options':[],'validation':{}},'created_at':now()})
    initialize(db)
    assert client.get('/api/questions/'+str(vid),headers=student).status_code==200
    client.delete('/api/questions/'+str(vid)+'/shares/migratestudent',headers=owner)
    initialize(db)
    assert client.get('/api/questions/'+str(vid),headers=student).status_code==404
    assert db.question_versions.count_documents({})==1


@pytest.mark.case(title='确认模型 并发批量转发幂等',input='四路同时转发同一题目到同一账号',expected='均成功，授权只有一人，接收者尚未收录')
def test_confirmed_concurrent_forward(client,owner):
    student=login(client,'concurrentreceiver');v=saved(client,owner)
    def transfer(_):
        return client.post('/api/questions/forward',headers=owner,json={'username':'concurrentreceiver','items':[pair(v)]})
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(transfer,range(4)))
    assert all(r.status_code==200 for r in results)
    assert client.get('/api/usual-questions',headers=student).json()['total']==0
    assert len(client.app.state.db.questions.find_one({'_id':ObjectId(v['_id'])})['shared_with'])==1


@pytest.mark.case(title='确认模型 问卷新版本并发保护',input='四路从同一已发布问卷创建新版本',expected='四个不同版本均创建成功，编号2至5唯一；原版保留，多份草稿可并存')
def test_confirmed_concurrent_survey_version(client,owner):
    s=create(client,owner);publish(client,owner,s)
    def new(_):return client.post('/api/surveys/'+s['_id']+'/versions',headers=owner)
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(new,range(4)))
    assert all(r.status_code==201 for r in results),[r.text for r in results]
    assert {r.json()['survey_version'] for r in results}=={2,3,4,5}
    assert client.app.state.db.surveys.count_documents({})==5
