"""Resource composition, exact-version permissions and survey-local switching."""
from copy import deepcopy

from bson import ObjectId
import pytest

from .conftest import login
from .test_stage2 import saved
from .test_system import create, publish, q, submit
from .test_confirmed_model import pair


def next_question(client, owner, source, **changes):
    response=client.post('/api/questions/'+source['_id']+'/versions',headers=owner,
        json={**source['content'], **changes})
    assert response.status_code==201,response.text
    return response.json()


def source_question(version,local='shared',**extra):
    return {'id':local,**version['content'],**pair(version),'source_version_id':version['_id'],
        'jump_enabled':False,'rules':[],**extra}


@pytest.mark.case(title='题目模块 完整版本与精确授权',input='作者建立两版；仅共享v1；各账号查询同一题目版本列表',expected='作者可见两版完整内容，接收者仅v1，陌生人404，未登录401')
def test_module_version_permissions(client,owner):
    first=saved(client,owner,title='旧年龄');second=next_question(client,owner,first,title='新年龄',validation={'minimum':18,'maximum':80,'integer_only':True})
    receiver=login(client,'versionreader');other=login(client,'versionother')
    client.post('/api/questions/'+first['_id']+'/shares',headers=owner,json={'username':'versionreader'})
    url='/api/questions/'+first['question_id']+'/versions'
    versions=client.get(url,headers=owner).json()['items']
    assert [v['question_version'] for v in versions]==[1,2]
    assert versions[1]['content']==second['content']
    assert [v['question_version'] for v in client.get(url,headers=receiver).json()['items']]==[1]
    assert client.get(url,headers=other).status_code==404
    assert client.get(url).status_code==401


@pytest.mark.case(title='问卷模块 保存原草稿再用题建新卷',input='未设截止的原草稿保存标题与普通题→选两个已有版本创建新问卷',expected='原草稿保存、ID不变；新卷独立ID、含选题快照，无截止也能继续编辑')
def test_module_draft_then_new_survey(client,owner):
    original=client.post('/api/surveys/drafts',headers=owner).json()
    body={'title':'保留编辑中的原卷','description':'未完成','allow_anonymous':False,'allow_multiple':False,
        'deadline':None,'questions':[q('old','text',title='原来的问题')],'revision':1}
    result=client.patch('/api/surveys/'+original['_id']+'/draft-content',headers=owner,json=body)
    assert result.status_code==200,result.text
    first=saved(client,owner);second=saved(client,owner,'text')
    result=client.post('/api/surveys/from-questions',headers=owner,json={'items':[pair(first),pair(second)]})
    assert result.status_code==201,result.text
    new=result.json()
    assert new['_id']!=original['_id'] and new['survey_id']!=original['survey_id']
    assert new['deadline'] is None and new['status']=='draft' and new['survey_version']==1
    assert [v['id'] for v in new['questions']]==['q1','q2']
    old=client.get('/api/surveys/'+original['_id'],headers=owner).json()
    assert old['title']==body['title'] and old['questions'][0]['title']=='原来的问题' and old['status']=='draft'
    assert client.post('/api/surveys/'+new['_id']+'/publish',headers=owner).status_code==422
    assert client.patch('/api/surveys/'+original['_id']+'/draft-content',headers=owner,json=body).status_code==409


@pytest.mark.case(title='按题建卷 共享可用与失败不建卷',input='接收者用已共享题建卷；批次中混入未共享题、空选择',expected='授权题能使用；其余404/422且不新增问卷')
def test_module_create_permissions(client,owner):
    shared=saved(client,owner);private=saved(client,owner)
    receiver=login(client,'newfromshared')
    client.post('/api/questions/'+shared['_id']+'/shares',headers=owner,json={'username':'newfromshared'})
    assert client.post('/api/surveys/from-questions',headers=receiver,json={'items':[pair(shared)]}).status_code==201
    count=client.app.state.db.surveys.count_documents({})
    assert client.post('/api/surveys/from-questions',headers=receiver,json={'items':[pair(shared),pair(private)]}).status_code==404
    assert client.post('/api/surveys/from-questions',headers=receiver,json={'items':[]}).status_code==422
    assert client.app.state.db.surveys.count_documents({})==count


@pytest.mark.case(title='切题版本 保留本地ID和兼容跳转',input='本卷两题跳转相连；切换目标题同题型新版本',expected='本地ID、入向及出向规则保留；只改目标内容，版本修订与权限校验有效')
def test_module_switch_preserves_routing(client,owner):
    first=saved(client,owner,'single',title='旧选项题');second=next_question(client,owner,first,title='新选项题')
    s=create(client,owner,[q('gate',rules=[{'op':'eq','value':'a','target':'target'}],jump_enabled=True),
        source_question(first,'target',jump_enabled=True,rules=[{'op':'eq','value':'a','target':'END'}])])
    url='/api/surveys/'+s['_id']+'/questions/target/version'
    r=client.put(url,headers=owner,json={**pair(second),'revision':s['revision']})
    assert r.status_code==200,r.text
    changed=r.json()
    assert changed['questions'][0]==s['questions'][0]
    assert changed['questions'][1]['id']=='target' and changed['questions'][1]['rules']==s['questions'][1]['rules']
    assert changed['questions'][1]['title']=='新选项题' and changed['survey_version']==1
    assert client.put(url,headers=owner,json={**pair(first),'revision':1}).status_code==409
    foreign=saved(client,owner)
    assert client.put(url,headers=owner,json={**pair(foreign),'revision':changed['revision']}).status_code==422
    other=login(client,'switchoutsider')
    assert client.put(url,headers=other,json={**pair(first),'revision':changed['revision']}).status_code==404


@pytest.mark.parametrize('kind',['type','options'])
@pytest.mark.case(title='切题版本 跳转不兼容须明确清除',input='有单选跳转的题切换为文本或移除被引用选项的版本',expected='默认422且问卷不变；明确clear_rules后切换成功，本地ID和其他题规则保留')
def test_module_incompatible_routing(client,owner,kind):
    first=saved(client,owner,'single')
    changes={'type':'text','options':[]} if kind=='type' else {'options':[{'id':'c','label':'新C'},{'id':'d','label':'新D'}]}
    second=next_question(client,owner,first,**changes)
    s=create(client,owner,[source_question(first,rules=[{'op':'eq','value':'a','target':'END'}],jump_enabled=True)])
    db=client.app.state.db;before=deepcopy(db.surveys.find_one({'_id':ObjectId(s['_id'])}))
    url='/api/surveys/'+s['_id']+'/questions/shared/version';body={**pair(second),'revision':1}
    assert client.put(url,headers=owner,json=body).status_code==422
    assert db.surveys.find_one({'_id':ObjectId(s['_id'])})==before
    r=client.put(url,headers=owner,json={**body,'clear_rules':True})
    assert r.status_code==200,r.text
    assert r.json()['questions'][0]['rules']==[] and r.json()['questions'][0]['jump_enabled'] is False


@pytest.mark.case(title='已发布问卷 换题另建版本保留答卷',input='v1答10；直接换题；用v2题内容新建问卷草稿',expected='直接修改409；生成问卷v2草稿，新题版本2；旧问卷与答卷逐文档不变')
def test_module_published_switch_new_version(client,owner):
    first=saved(client,owner);second=next_question(client,owner,first,title='新版数字题')
    s=create(client,owner,[source_question(first)]);publish(client,owner,s)
    assert submit(client,owner,s,{'shared':10}).status_code==201
    db=client.app.state.db;old=deepcopy(db.surveys.find_one({'_id':ObjectId(s['_id'])}));answer=deepcopy(db.responses.find_one())
    assert client.put('/api/surveys/'+s['_id']+'/questions/shared/version',headers=owner,json={**pair(second),'revision':s['revision']}).status_code==409
    r=client.post('/api/surveys/'+s['_id']+'/versions',headers=owner,json={**pair(second),'local_id':'shared'})
    assert r.status_code==201,r.text
    assert r.json()['status']=='draft' and r.json()['survey_version']==2
    assert r.json()['questions'][0]['question_version']==2
    assert db.surveys.find_one({'_id':old['_id']})==old and db.responses.find_one()==answer


@pytest.mark.case(title='未授权版本不能切入自己的问卷',input='学生用v1建卷；作者v2未共享；学生伪造切换v2',expected='404且草稿、版本引用和修订不变')
def test_module_switch_unshared_version(client,owner):
    first=saved(client,owner);second=next_question(client,owner,first)
    student=login(client,'switchstudent')
    client.post('/api/questions/'+first['_id']+'/shares',headers=owner,json={'username':'switchstudent'})
    s=client.post('/api/surveys/from-questions',headers=student,json={'items':[pair(first)]}).json()
    before=client.app.state.db.surveys.find_one({'_id':ObjectId(s['_id'])})
    r=client.put('/api/surveys/'+s['_id']+'/questions/q1/version',headers=student,json={**pair(second),'revision':1})
    assert r.status_code==404
    assert client.app.state.db.surveys.find_one({'_id':ObjectId(s['_id'])})==before
