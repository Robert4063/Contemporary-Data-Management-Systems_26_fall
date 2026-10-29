"""Explicit per-user/per-version collection, removal and retired named banks."""
from copy import deepcopy

from bson import ObjectId
import pytest

from .conftest import login
from .test_confirmed_model import pair
from .test_stage2 import saved, reused
from .test_system import publish, submit


def create_private(client,headers,title='未入库的题'):
    r=client.post('/api/questions',headers=headers,json={'title':title,'type':'number'})
    assert r.status_code==201,r.text
    return r.json()


@pytest.mark.case(title='题库 显式收录与独立版本存放',input='创建v1→修改v2→恢复v3→显式收录v1和v2',expected='创建修改恢复均不自动入库；每个收录是一用户一版本单独记录，重复入库不重复')
def test_collection_requires_explicit_action(client,owner):
    v1=create_private(client,owner)
    v2=client.post('/api/questions/'+v1['_id']+'/versions',headers=owner,json={**v1['content'],'title':'第二版'}).json()
    v3=client.post('/api/questions/'+v1['_id']+'/restore',headers=owner).json()
    assert client.get('/api/usual-questions',headers=owner).json()['total']==0
    assert client.app.state.db.usual_questions.count_documents({})==0
    for version in (v1,v2,v1):
        assert client.post('/api/usual-questions',headers=owner,json=pair(version)).status_code==201
    rows=list(client.app.state.db.usual_questions.find())
    assert len(rows)==2 and all('content' not in row and 'items' not in row and row['in_library'] for row in rows)
    assert {row['question_version'] for row in rows}=={1,2}
    assert v3['question_version']==3 and client.app.state.db.questions.count_documents({})==3
    assert len({row['question_id'] for row in rows})==1


@pytest.mark.case(title='共享授权不等于收录',input='未收录题转发→接收者主动入库→尝试再次转发→原作者撤销',expected='双方起初题库均为空；接收者入库仅增加自己的记录，不能转授权；撤销后不泄露内容且可移除失效收录')
def test_forwarding_requires_receiver_collection(client,owner):
    v=create_private(client,owner);receiver=login(client,'collectreceiver');login(client,'collectthird')
    client.post('/api/questions/forward',headers=owner,json={'username':'collectreceiver','items':[pair(v)]})
    for headers in (owner,receiver):
        assert client.get('/api/usual-questions',headers=headers).json()['total']==0
    assert client.app.state.db.usual_questions.count_documents({})==0
    assert client.get('/api/questions',headers=receiver).json()['total']==1
    assert client.post('/api/usual-questions',headers=receiver,json=pair(v)).status_code==201
    assert client.get('/api/usual-questions',headers=receiver).json()['total']==1
    assert client.get('/api/usual-questions',headers=owner).json()['total']==0
    assert client.post('/api/questions/forward',headers=receiver,json={'username':'collectthird','items':[pair(v)]}).status_code==403
    client.delete('/api/questions/'+v['_id']+'/shares/collectreceiver',headers=owner)
    unavailable=client.get('/api/usual-questions',headers=receiver).json()['items'][0]
    assert unavailable['unavailable'] is True and 'content' not in unavailable
    assert client.get('/api/questions',headers=receiver).json()['total']==0
    assert client.delete('/api/usual-questions/'+v['question_id']+'/1',headers=receiver).status_code==200


@pytest.mark.case(title='题库 按版本或整题删除只影响自己的收录',input='两版已入库，v1已发布作答且共享收录；删v2、删整题→重启初始化→重新入库',expected='题库删除生效、不在待入库反复出现；题目历史/已发布问卷/答卷/其他用户收录不变，可显式重新收录')
def test_removing_collection_preserves_history(client,owner):
    from app.db import initialize
    v1=saved(client,owner);v2=client.post('/api/questions/'+v1['_id']+'/versions',headers=owner,json={**v1['content'],'title':'新版'}).json()
    client.post('/api/usual-questions',headers=owner,json=pair(v2))
    survey=reused(client,owner,v1);publish(client,owner,survey);submit(client,owner,survey,{'q1':12})
    receiver=login(client,'keptcollector');stranger=login(client,'removeoutsider')
    client.post('/api/questions/'+v1['_id']+'/shares',headers=owner,json={'username':'keptcollector'})
    client.post('/api/usual-questions',headers=receiver,json=pair(v1))
    db=client.app.state.db;before=deepcopy(db.surveys.find_one());answer=deepcopy(db.responses.find_one());versions=list(db.questions.find())
    assert client.delete('/api/usual-questions/'+v1['question_id']+'/1',headers=stranger).status_code==404
    assert client.delete('/api/usual-questions/'+v1['question_id']+'/2',headers=owner).json()['removed_count']==1
    assert client.get('/api/usual-questions',headers=owner).json()['total']==1
    assert client.delete('/api/usual-questions/'+v1['question_id'],headers=owner).json()['removed_count']==1
    initialize(db)
    assert client.get('/api/usual-questions',headers=owner).json()['total']==0
    assert client.get('/api/questions',headers=owner).json()['total']==0
    assert db.surveys.find_one()==before and db.responses.find_one()==answer and list(db.questions.find())==versions
    assert client.get('/api/usual-questions',headers=receiver).json()['total']==1
    assert client.get('/api/questions/'+v1['_id'],headers=receiver).status_code==200
    assert client.get('/api/surveys/'+survey['_id']+'/statistics/q1',headers=owner).json()['average']==12
    assert client.post('/api/usual-questions',headers=owner,json=pair(v1)).status_code==201
    assert client.get('/api/usual-questions',headers=owner).json()['total']==1


@pytest.mark.case(title='旧收录需确认、命名题库停用且不删旧数据',input='旧收录缺少显式标志、旧命名题库文档→初始化；请求原命名题库路径',expected='不擅自确认旧收录，原内容授权保留；旧命名题库原样留档，新接口不存在')
def test_legacy_collection_and_retired_banks(client,owner):
    from app.db import initialize,SCHEMAS
    v=saved(client,owner);db=client.app.state.db
    receiver=login(client,'legacycollectreader')
    receiver_id=ObjectId(client.get('/api/auth/me',headers=receiver).json()['id'])
    db.usual_questions.update_many({}, {'$unset':{'in_library':''}, '$set': {'shared_with':[receiver_id]}})
    db.questions.update_one({'_id':ObjectId(v['_id'])},{'$unset':{'shared_with':''}})
    old={'owner_id':ObjectId(v['owner_id']),'name':'历史命名库','items':[{'question_id':ObjectId(v['question_id']),'question_version':1}]}
    db.question_banks.insert_one(old);before=deepcopy(db.question_banks.find_one())
    initialize(db)
    assert len(SCHEMAS)==6 and 'question_banks' not in SCHEMAS
    assert db.question_banks.find_one()==before
    assert client.get('/api/usual-questions',headers=owner).json()['total']==0
    assert client.get('/api/questions/'+v['_id'],headers=owner).status_code==200
    assert client.get('/api/questions/'+v['_id'],headers=receiver).status_code==200
    client.delete('/api/questions/'+v['_id']+'/shares/legacycollectreader',headers=owner)
    initialize(db)
    assert client.get('/api/questions/'+v['_id'],headers=receiver).status_code==404
    for method,path in [('get','/api/question-banks'),('post','/api/question-banks'),('delete','/api/question-banks/'+str(old['_id']))]:
        assert getattr(client,method)(path,headers=owner).status_code==404
