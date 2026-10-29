"""The old and new questionnaire drafts can evolve independently."""
from copy import deepcopy

from bson import ObjectId
import pytest

from .conftest import login
from .test_confirmed_model import pair
from .test_module_flow import source_question
from .test_stage2 import saved
from .test_system import create, q


def save_title(client,headers,survey,title):
    body={k:deepcopy(survey[k]) for k in ('title','description','deadline','allow_anonymous','allow_multiple','questions','revision')}
    body['questions'][0]['title']=title
    result=client.patch('/api/surveys/'+survey['_id']+'/draft-content',headers=headers,json=body)
    assert result.status_code==200,result.text
    return result.json()


@pytest.mark.case(title='按题生成下一版本 两版草稿都能编辑且互不影响',input='原卷两题→从其中一题创建下一版→分别改题保存→重读两卷',expected='新版本保留全部题；两份草稿独立保存，题库原版本保持不变')
def test_editable_old_and_new_drafts(client,owner):
    version=saved(client,owner,title='共同的初始题')
    old=create(client,owner,[source_question(version,'selected'),q('another','text',title='只留在旧卷的题')])
    new=client.post('/api/surveys/'+old['_id']+'/versions',headers=owner,json={'revision':old['revision'],'local_id':'selected'}).json()
    assert old['survey_id']==new['survey_id'] and new['survey_version']==2 and new['questions'][0]['title']=='共同的初始题'
    assert len(new['questions'])==2
    changed_new=save_title(client,owner,new,'新问卷自己的题干')
    changed_old=save_title(client,owner,old,'旧问卷自己的题干')
    for s,title,number in [(changed_new,'新问卷自己的题干',2),(changed_old,'旧问卷自己的题干',1)]:
        current=client.get('/api/surveys/'+s['_id'],headers=owner).json()
        assert current['status']=='draft' and current['questions'][0]['title']==title
        assert current['survey_version']==number
    assert client.get('/api/questions/'+version['_id'],headers=owner).json()['content']['title']=='共同的初始题'
    old_now=client.get('/api/surveys/'+old['_id'],headers=owner).json()
    assert old_now['questions'][1]==old['questions'][1]
    assert changed_new['questions'][0]['question_version']!=changed_old['questions'][0]['question_version']


@pytest.mark.case(title='共享题 草稿编辑派生自己的题且保留原授权边界',input='接收者用共享题建卷并修改；第三人尝试修改；伪造新引用内容',expected='修改生成接收者题目身份并记parent；作者题目不变；越权404，伪造新引用422')
def test_editable_shared_copy_preserves_author(client,owner):
    version=saved(client,owner,title='老师的题')
    student=login(client,'editable_student');outsider=login(client,'editable_outsider')
    client.post('/api/questions/'+version['_id']+'/shares',headers=owner,json={'username':'editable_student'})
    survey=client.post('/api/surveys/from-questions',headers=student,json={'items':[pair(version)]}).json()
    changed=save_title(client,student,survey,'学生问卷中的题')
    copied=changed['questions'][0]
    assert copied['question_id']!=version['question_id']
    doc=client.app.state.db.questions.find_one({'question_id':ObjectId(copied['question_id']),'question_version':copied['question_version']})
    assert doc['parent']=={'question_id':ObjectId(version['question_id']),'question_version':1}
    student_id=client.get('/api/auth/me',headers=student).json()['id']
    assert str(doc['owner_id'])==student_id
    assert client.get('/api/questions/'+version['_id'],headers=owner).json()['content']['title']=='老师的题'
    body={k:changed[k] for k in ('title','description','deadline','allow_anonymous','allow_multiple','questions','revision')}
    assert client.patch('/api/surveys/'+survey['_id']+'/draft-content',headers=outsider,json=body).status_code==404
    forged=source_question(version,title='捏造来源的内容')
    assert client.post('/api/surveys',headers=student,json={'title':'非法新卷','deadline':create(client,student)['deadline'],'questions':[forged]}).status_code==422


@pytest.mark.case(title='下一问卷版本 完整保留所有题目、顺序、规则和设置',input='含三题及跨题跳转的草稿→从中间题创建下一版→继续生成v3',expected='同一survey_id、版本2/3、父版本1/2；两次完整保留题目快照，旧稿逐文档不变，无答卷复制')
def test_next_version_preserves_every_question(client,owner):
    version=saved(client,owner,title='中间的依据题')
    old=create(client,owner,[q('gate',rules=[{'op':'eq','value':'a','target':'last'}],jump_enabled=True),
        source_question(version,'basis'),q('last','text')],description='保留说明',allow_anonymous=True,allow_multiple=True)
    db=client.app.state.db;before=deepcopy(db.surveys.find_one({'_id':ObjectId(old['_id'])}))
    current=old
    for number in (2,3):
        r=client.post('/api/surveys/'+current['_id']+'/versions',headers=owner,json={'revision':current['revision'],'local_id':'basis'})
        assert r.status_code==201,r.text
        new=r.json()
        assert new['survey_id']==old['survey_id'] and new['survey_version']==number and new['parent_version']==number-1
        assert new['status']=='draft' and new['questions']==old['questions']
        for field in ('title','description','deadline','allow_anonymous','allow_multiple'):
            assert new[field]==old[field]
        current=new
    assert db.surveys.find_one({'_id':before['_id']})==before
    assert db.surveys.count_documents({'survey_id':ObjectId(old['survey_id']),'status':'draft'})==3
    assert db.responses.count_documents({})==0


@pytest.mark.case(title='下一问卷版本 权限、修订和附加选题',input='陈旧revision、他人问卷、无效本地题号；从题库选题后建下一版',expected='冲突409、越权404、不存在题号404；加入选题时保留原题，已存在的版本不重复添加')
def test_next_version_permissions_and_selection(client,owner):
    old=create(client,owner,[q('original','text')]);version=saved(client,owner)
    other=login(client,'nextversionoutsider');url='/api/surveys/'+old['_id']+'/versions'
    assert client.post(url,headers=owner,json={'revision':99,'local_id':'original'}).status_code==409
    assert client.post(url,headers=other,json={'revision':1,'local_id':'original'}).status_code==404
    assert client.post(url,headers=owner,json={'revision':1,'local_id':'missing'}).status_code==404
    assert client.app.state.db.surveys.count_documents({})==1
    r=client.post(url,headers=owner,json={'revision':1,'selected_question':pair(version)})
    assert r.status_code==201,r.text
    new=r.json()
    assert new['questions'][0]==old['questions'][0] and len(new['questions'])==2
    r=client.post('/api/surveys/'+new['_id']+'/versions',headers=owner,json={'revision':1,'selected_question':pair(version)})
    assert r.status_code==201 and len(r.json()['questions'])==2


@pytest.mark.case(title='旧库升级 移除单草稿唯一限制且保留版本唯一',input='重建旧版survey_id/status单草稿索引→初始化→创建下一版草稿',expected='只移除过时索引；两版草稿共存，survey_id/survey_version唯一约束继续有效')
def test_next_version_upgrades_old_draft_index(client,owner):
    from app.db import initialize
    db=client.app.state.db;old=create(client,owner)
    name=db.surveys.create_index([('survey_id',1),('status',1)],unique=True,
        partialFilterExpression={'status':'draft','survey_id':{'$type':'objectId'}})
    initialize(db)
    assert name not in db.surveys.index_information()
    assert client.post('/api/surveys/'+old['_id']+'/versions',headers=owner,json={'revision':1,'local_id':'q1'}).status_code==201
    assert any(index.get('unique') and list(index['key'])==['survey_id','survey_version'] for index in db.surveys.list_indexes())
