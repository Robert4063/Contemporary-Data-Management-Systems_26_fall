"""Regression tests for the audit findings and the user's three design decisions."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from datetime import datetime, timedelta

import pytest
from bson import ObjectId

from .conftest import login
from .test_system import create, publish, q, submit
from app.security import now


@pytest.mark.case(title='超大整数返回校验错误',input='数字答案及规则比较值=10**400',expected='422具体错误，无500，未保存非法答卷')
def test_extreme_numbers(client,owner):
    survey=create(client,owner,[q('n','number')]);publish(client,owner,survey)
    response=submit(client,owner,survey,{'n':10**400})
    assert response.status_code==422 and '有限数字' in response.text
    response=client.post('/api/surveys',headers=owner,json={'title':'rule','deadline':(now()+timedelta(days=1)).isoformat(),'questions':[q('n','number',rules=[{'op':'gt','value':10**400,'target':'END'}])]})
    assert response.status_code==422
    assert client.app.state.db.responses.count_documents({})==0


@pytest.mark.parametrize('body',[
    {'title':'   '},
    {'title':'normal','questions':[q('q1',title=' \n ')]},
    {'title':'normal','questions':[q('q1',options=[{'id':'a','label':'  '},{'id':'b','label':'B'}])]},
])
@pytest.mark.case(title='拒绝空白标题和选项',input='问卷/题目标题或选项全部为空白',expected='422，拒绝保存无意义配置')
def test_blank_labels(client,owner,body):
    assert client.post('/api/surveys',headers=owner,json={'deadline':(now()+timedelta(days=1)).isoformat(),**body}).status_code==422


@pytest.mark.case(title='发布后仅修改标题说明',input='PATCH元数据；携带questions；非所有者；旧版本',expected='合法200并递增版本；结构字段422；越权404；旧版本409；答卷与规则不变')
def test_metadata(client,owner):
    survey=create(client,owner);publish(client,owner,survey)
    assert submit(client,owner,survey,{'q1':'a'}).status_code==201
    url='/api/surveys/'+survey['_id']+'/metadata'
    payload={'title':'修正标题','description':'修正说明','revision':survey['revision']}
    response=client.patch(url,headers=owner,json=payload)
    assert response.status_code==200
    doc=response.json()
    assert doc['revision']==survey['revision']+1 and doc['questions']==survey['questions']
    assert doc['status']=='published' and not doc['allow_multiple']
    assert client.patch(url,headers=owner,json=payload).status_code==409
    assert client.patch(url,headers=owner,json={**payload,'questions':[]}).status_code==422
    other=login(client,'auditother')
    assert client.patch(url,headers=other,json=payload).status_code==404
    assert client.get('/api/surveys/'+survey['_id']+'/statistics',headers=owner).json()['response_count']==1


def post_attempt(client,headers,survey,payload):
    return client.post('/api/public/surveys/'+survey['_id']+'/responses',headers=headers,json=payload)


@pytest.mark.parametrize('allow_multiple',[False,True])
@pytest.mark.case(title='提交重试幂等与主动再次填写',input='同一个UUID重试；同UUID改答案；新UUID再次填写',expected='首次201重试200且ID相同；改答案409；新填写按每账号策略处理')
def test_idempotent_retries(client,owner,allow_multiple):
    survey=create(client,owner,allow_multiple=allow_multiple);publish(client,owner,survey)
    payload={'answers':{'q1':'a'},'submission_id':str(uuid4())}
    first=post_attempt(client,owner,survey,payload)
    retry=post_attempt(client,owner,survey,payload)
    assert first.status_code==201 and retry.status_code==200
    assert first.json()['id']==retry.json()['id'] and retry.json()['replayed']
    assert post_attempt(client,owner,survey,{**payload,'answers':{'q1':'b'}}).status_code==409
    second=post_attempt(client,owner,survey,{**payload,'submission_id':str(uuid4())})
    assert second.status_code==(201 if allow_multiple else 409)
    count=client.app.state.db.responses.count_documents({'survey_id':ObjectId(survey['_id'])})
    assert count==(2 if allow_multiple else 1)


@pytest.mark.case(title='并发重试只落一份且关闭后可查询成功结果',input='8个同UUID并发；问卷关闭后重试',expected='1个201和7个200，ID相同，仅1份答卷；关闭后重试200')
def test_concurrent_retry_and_close(client,owner):
    survey=create(client,owner,allow_anonymous=True,allow_multiple=True);publish(client,owner,survey)
    payload={'answers':{'q1':'a'},'anonymous':True,'submission_id':str(uuid4())}
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(lambda _:post_attempt(client,owner,survey,payload),range(8)))
    codes=[r.status_code for r in responses]
    assert codes.count(201)==1 and codes.count(200)==7
    assert len({r.json()['id'] for r in responses})==1
    doc=client.app.state.db.responses.find_one({'survey_id':ObjectId(survey['_id'])})
    assert 'respondent_id' not in doc and 'respondent_key' not in doc
    assert 'submission_id' not in doc and len(doc['submission_key'])==64
    assert client.post('/api/surveys/'+survey['_id']+'/close',headers=owner).status_code==200
    assert post_attempt(client,owner,survey,payload).status_code==200
    assert post_attempt(client,owner,survey,{**payload,'submission_id':str(uuid4())}).status_code==410
    assert client.app.state.db.responses.count_documents({})==1


@pytest.mark.case(title='缺失提交ID及跨账号作用域',input='不带UUID；两个账号使用相同UUID',expected='缺失422；相同UUID在不同账号分别保存，不泄露他人答卷')
def test_attempt_identity_scope(client,owner):
    survey=create(client,owner);publish(client,owner,survey)
    assert post_attempt(client,owner,survey,{'answers':{'q1':'a'}}).status_code==422
    payload={'answers':{'q1':'a'},'submission_id':str(uuid4())}
    first=post_attempt(client,owner,survey,payload)
    other=login(client,'scopevisitor')
    second=post_attempt(client,other,survey,payload)
    assert first.status_code==second.status_code==201 and first.json()['id']!=second.json()['id']


@pytest.mark.case(title='创建按钮立即保存服务器时间',input='先建空草稿，再编辑保存；尝试传入created_at',expected='创建时已持久化created_at；后续不变；客户端不能伪造创建时间')
def test_creation_time_and_immutability(client,owner):
    before=now()-timedelta(milliseconds=1)
    response=client.post('/api/surveys/drafts',headers=owner)
    assert response.status_code==201
    survey=response.json()
    created=datetime.fromisoformat(survey['created_at'])
    assert before<=created<=now()
    stored=client.app.state.db.surveys.find_one({'_id':ObjectId(survey['_id'])})
    assert stored['created_at']==created and stored['questions']==[]
    fields=['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']
    body={k:survey[k] for k in fields}
    body.update(title='完成标题',questions=[q('q1')],deadline=(now()+timedelta(days=1)).isoformat())
    saved=client.put('/api/surveys/'+survey['_id'],headers=owner,json=body)
    assert saved.status_code==200 and saved.json()['created_at']==survey['created_at']
    assert client.put('/api/surveys/'+survey['_id'],headers=owner,json={**body,'created_at':'2000-01-01T00:00:00Z'}).status_code==422
    assert client.post('/api/surveys',headers=owner,json={'title':'injected','created_at':'2000-01-01T00:00:00Z'}).status_code==422


@pytest.mark.parametrize('deadline',[0,True,'2027-01-01','2027-01-01T00:00:00','2027-13-40T25:00:00Z','tomorrow'])
@pytest.mark.case(title='时间格式严格校验',input='数值/布尔时间戳、仅日期、无时区、无效年月日、任意文本',expected='创建与截止修改接口均422')
def test_deadline_format(client,owner,deadline):
    survey=create(client,owner)
    assert client.post('/api/surveys',headers=owner,json={'title':'bad date','deadline':deadline}).status_code==422
    assert client.patch('/api/surveys/'+survey['_id']+'/deadline',headers=owner,json={'deadline':deadline}).status_code==422


@pytest.mark.case(title='截止不得早于创建时间且不得设置过期',input='创建前1秒；创建后但当前前的时间；合法未来ISO8601时间',expected='保存/截止设置422；合法未来200；发布前过期再次422')
def test_deadline_bounds_and_publication(client,owner):
    survey=create(client,owner)
    created=datetime.fromisoformat(survey['created_at'])
    past=(created-timedelta(seconds=1)).isoformat()
    base='/api/surveys/'+survey['_id']
    response=client.patch(base+'/deadline',headers=owner,json={'deadline':past})
    assert response.status_code==422 and '创建时间' in response.text
    fields=['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']
    body={k:survey[k] for k in fields}
    assert client.put(base,headers=owner,json={**body,'deadline':past}).status_code==422
    future=(now()+timedelta(days=1)).isoformat()
    assert client.patch(base+'/deadline',headers=owner,json={'deadline':future}).status_code==200
    # Simulate time passing without a fragile wall-clock sleep.
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':now()-timedelta(days=1)}})
    assert client.post(base+'/publish',headers=owner).status_code==422
    assert client.get(base,headers=owner).json()['status']=='draft'


@pytest.mark.case(title='发布重新验证数据库全部题目配置',input='数据库中遗留非法跳转目标和空白题目',expected='发布422，状态仍draft，不能仅依赖曾经保存通过')
def test_publish_revalidates_configuration(client,owner):
    survey=create(client,owner)
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{
        'questions.0.title':' ', 'questions.0.rules':[{'op':'eq','value':'a','target':'missing'}]}})
    response=client.post('/api/surveys/'+survey['_id']+'/publish',headers=owner)
    assert response.status_code==422 and '不能发布' in response.text
    assert client.get('/api/surveys/'+survey['_id'],headers=owner).json()['status']=='draft'


@pytest.mark.parametrize('payload',[{}, {'deadline':None}, {'deadline':''}])
@pytest.mark.case(title='截止时间不得省略或清空',input='省略/null/空字符串截止；创建、保存、截止修改',expected='全部422；不能清除已有截止；初建空草稿仍可记录创建时间')
def test_deadline_is_required(client,owner,payload):
    response=client.post('/api/surveys',headers=owner,json={'title':'required date',**payload})
    assert response.status_code==422
    survey=create(client,owner)
    fields=['title','description','allow_anonymous','allow_multiple','questions','revision']
    body={k:survey[k] for k in fields}
    assert client.put('/api/surveys/'+survey['_id'],headers=owner,json={**body,**payload}).status_code==422
    assert client.patch('/api/surveys/'+survey['_id']+'/deadline',headers=owner,json=payload).status_code==422
    actual=client.get('/api/surveys/'+survey['_id'],headers=owner).json()
    assert actual['deadline']==survey['deadline']
    draft=client.post('/api/surveys/drafts',headers=owner)
    assert draft.status_code==201 and draft.json()['created_at'] and draft.json()['deadline'] is None
    assert client.post('/api/surveys/'+draft.json()['_id']+'/publish',headers=owner).status_code==422


@pytest.mark.case(title='旧问卷无截止时拒绝接收并可补齐',input='模拟旧published且deadline=null；读取、填写；所有者补截止',expected='补齐前410且不接收答卷；补齐合法未来时间后恢复读取与提交')
def test_legacy_missing_deadline(client,owner):
    survey=create(client,owner);publish(client,owner,survey)
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':None}})
    base='/api/surveys/'+survey['_id']
    assert client.get('/api/public/surveys/'+survey['_id'],headers=owner).status_code==410
    assert submit(client,owner,survey,{'q1':'a'}).status_code==410
    assert client.patch(base+'/deadline',headers=owner,json={'deadline':(now()+timedelta(days=1)).isoformat()}).status_code==200
    assert client.get('/api/public/surveys/'+survey['_id'],headers=owner).status_code==200
    assert submit(client,owner,survey,{'q1':'a'}).status_code==201


@pytest.mark.case(title='未知统计探测路径不影响业务API',input='GET /hybridaction/zybTrackerStatisticsAction 和 GET /api/auth/me',expected='未定义路径404；有效账号me仍200；不伪造外部接口')
def test_unknown_tracker_path(client,owner):
    response=client.get('/hybridaction/zybTrackerStatisticsAction?data=%7B%7D&__callback__=test')
    assert response.status_code==404
    assert client.get('/api/auth/me',headers=owner).status_code==200
    assert client.get('/api/health').json()=={'status':'ok'}


@pytest.mark.case(title='多选全都不包含条件',input='指定A/B；仅选C、选A/C；空规则值；非多选题配置',expected='仅C跳过必答题结束；A/C进入后续题；非法条件422')
def test_contains_none_branch(client,owner):
    question=q('m','multiple',options=[{'id':x,'label':x.upper()} for x in 'abc'],
        rules=[{'op':'contains_none','value':['a','b'],'target':'END'}])
    survey=create(client,owner,[question,q('t','text')],allow_multiple=True)
    publish(client,owner,survey)
    endpoint='/api/public/surveys/'+survey['_id']+'/next'
    result=client.post(endpoint,headers=owner,json={'answers':{'m':['c']}}).json()
    assert result['complete'] and result['path']==['m']
    assert submit(client,owner,survey,{'m':['c']}).status_code==201
    result=client.post(endpoint,headers=owner,json={'answers':{'m':['a','c']}}).json()
    assert not result['complete'] and result['next_question_id']=='t'
    assert submit(client,owner,survey,{'m':['a','c']}).status_code==422
    assert submit(client,owner,survey,{'m':['a','c'],'t':'正常后续'}).status_code==201
    for wrong in [q('m','multiple',rules=[{'op':'contains_none','value':[],'target':'END'}]),
                  q('s',rules=[{'op':'contains_none','value':['a'],'target':'END'}])]:
        assert client.post('/api/surveys',headers=owner,json={'title':'wrong','deadline':survey['deadline'],'questions':[wrong]}).status_code==422


@pytest.mark.parametrize('question',[
    q('q1','multiple',rules=[{'op':'contains_any','value':['a'],'target':'END'},{'op':'contains_none','value':['b'],'target':'END'}]),
    q('q1','number',rules=[{'op':'gt','value':1,'target':'END'},{'op':'lt','value':0,'target':'END'}]),
    q('q1',rules=[{'op':'eq','value':'a','target':'END'},{'op':'eq','value':'a','target':'END'}]),
])
@pytest.mark.case(title='后端拒绝第二条规则',input='多选两条、数字两条、同一个单选选项两条；创建/增题/编辑/发布',expected='全部422，非法配置不能创建或发布；单选不同选项目标仍允许')
def test_only_one_conditional_rule(client,owner,question):
    survey=create(client,owner,questions=[])
    body={k:survey[k] for k in ['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']}
    assert client.post('/api/surveys',headers=owner,json={'title':'invalid','deadline':survey['deadline'],'questions':[question]}).status_code==422
    base='/api/surveys/'+survey['_id']
    assert client.post(base+'/questions',headers=owner,json=question).status_code==422
    assert client.put(base,headers=owner,json={**body,'questions':[question]}).status_code==422
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'questions':[question]}})
    assert client.post(base+'/publish',headers=owner).status_code==422
    assert client.get(base,headers=owner).json()['status']=='draft'


@pytest.mark.parametrize('kind,value,rule',[
    ('single','a',{'op':'eq','value':'a','target':'END'}),
    ('multiple',['b'],{'op':'contains_none','value':['a'],'target':'END'}),
    ('number',5,{'op':'gt','value':0,'target':'END'}),
])
@pytest.mark.case(title='跳转开关实际控制后端路径',input='三种题型携带有效规则；先关闭保存再开启保存；发布后按规则填写',expected='关闭进入q2并保留规则；开启结束；MongoDB保存开关；合法答卷成功')
def test_jump_enabled(client,owner,kind,value,rule):
    survey=create(client,owner,[q('q1',kind,jump_enabled=False,rules=[rule]),q('q2','text')])
    base='/api/surveys/'+survey['_id']
    assert survey['questions'][0]['jump_enabled'] is False
    assert survey['questions'][0]['rules']==[rule]
    from app.logic import walk
    assert walk(survey['questions'],{'q1':value})['next_question_id']=='q2'
    body={k:survey[k] for k in ['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']}
    body['questions'][0]['jump_enabled']=True
    response=client.put(base,headers=owner,json=body)
    assert response.status_code==200,response.text
    assert client.app.state.db.surveys.find_one({'_id':ObjectId(survey['_id'])})['questions'][0]['jump_enabled'] is True
    publish(client,owner,survey)
    assert client.post('/api/public/surveys/'+survey['_id']+'/next',headers=owner,json={'answers':{'q1':value}}).json()['complete']
    assert submit(client,owner,survey,{'q1':value}).status_code==201


@pytest.mark.parametrize('invalid',[None,'false',0,1])
@pytest.mark.case(title='跳转开关类型严格校验',input='jump_enabled=null、字符串、整数',expected='后端422；只能使用JSON布尔值')
def test_jump_switch_validation(client,owner,invalid):
    survey=create(client,owner,questions=[])
    response=client.post('/api/surveys/'+survey['_id']+'/questions',headers=owner,json=q('q1',jump_enabled=invalid))
    assert response.status_code==422


@pytest.mark.case(title='跳转开关默认值与旧数据兼容',input='不传开关；无规则/有规则；历史MongoDB文档无开关；文本题强制开启',expected='无规则默认关闭；旧规则仍生效；文本题开启422')
def test_jump_legacy_and_default(client,owner):
    from app.models import Question
    from app.logic import walk
    assert Question.model_validate(q('q1')).jump_enabled is False
    rule={'op':'eq','value':'a','target':'END'}
    assert Question.model_validate(q('q1',rules=[rule])).jump_enabled is True
    historical=[q('q1',rules=[rule]),q('q2','text')]
    assert walk(historical,{'q1':'a'})['complete']
    survey=create(client,owner,[q('q1',jump_enabled=False,rules=[rule]),q('q2','text')])
    publish(client,owner,survey)
    endpoint='/api/public/surveys/'+survey['_id']+'/next'
    response=client.post(endpoint,headers=owner,json={'answers':{'q1':'a'}})
    assert response.json()['next_question_id']=='q2'
    assert submit(client,owner,survey,{'q1':'a'}).status_code==422
    assert submit(client,owner,survey,{'q1':'unknown','q2':'ok'}).status_code==422
    assert submit(client,owner,survey,{'q1':'a','q2':'ok'}).status_code==201
    assert client.post('/api/surveys',headers=owner,json={'title':'invalid','deadline':survey['deadline'],
        'questions':[q('q1','text',jump_enabled=True)]}).status_code==422


@pytest.mark.parametrize('state',['open','expired','closed','incomplete'])
@pytest.mark.case(title='登录后查看状态与填写权限分离',input='正常/截止/关闭/旧数据缺截止；未登录及登录的状态查询；尝试继续填写与提交',expected='未登录401；登录状态查询200并含只读题目；不可填时填写配置/下一步/提交410；答卷数不增加')
def test_survey_status(client,owner,state):
    survey=create(client,owner,title='状态检查',description='登录后可看状态')
    publish(client,owner,survey)
    changes={'open':{},'expired':{'deadline':now()-timedelta(seconds=1)},
        'closed':{'status':'closed'},'incomplete':{'deadline':None}}[state]
    if changes:
        client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':changes})
    base='/api/public/surveys/'+survey['_id']
    assert client.get(base+'/status').status_code==401
    visitor=login(client,'statusvisitor')
    response=client.get(base+'/status',headers=visitor)
    assert response.status_code==200
    data=response.json()
    assert set(data)=={'_id','title','description','status','deadline','created_at','questions','availability','can_fill','message','server_time'}
    assert data['created_at']==survey['created_at']
    assert data['questions'][0]['title']=='q1'
    assert set(data['questions'][0])=={'id','title','type','required','options','validation'}
    assert data['title']=='状态检查' and data['availability']==state
    assert data['can_fill'] is (state=='open')
    assert data['server_time']
    assert client.get('/survey/'+survey['_id']).status_code==200
    if state!='open':
        assert client.get(base,headers=visitor).status_code==410
        assert client.post(base+'/next',headers=visitor,json={'answers':{'q1':'a'}}).status_code==410
        assert submit(client,visitor,survey,{'q1':'a'}).status_code==410
        assert client.app.state.db.responses.count_documents({})==0


@pytest.mark.case(title='状态接口隐藏草稿与无效链接 截止边界',input='未发布/不存在/格式错误的ID；精确等于截止时刻',expected='登录后404；状态在截止瞬间为expired；填写410')
def test_survey_status_boundaries(client,owner,monkeypatch):
    survey=create(client,owner)
    base='/api/public/surveys/'+survey['_id']
    for sid in [survey['_id'],str(ObjectId()),'invalid']:
        assert client.get('/api/public/surveys/'+sid+'/status',headers=owner).status_code==404
    publish(client,owner,survey)
    instant=now().replace(microsecond=0)
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':instant}})
    monkeypatch.setattr('app.main.now',lambda:instant)
    data=client.get(base+'/status',headers=owner).json()
    assert data['availability']=='expired' and data['can_fill'] is False
    assert client.post(base+'/next',headers=owner,json={'answers':{'q1':'a'}}).status_code==410


@pytest.mark.parametrize('state',['expired','closed'])
@pytest.mark.case(title='截止关闭后只读详情包含四题型且不泄露答卷',input='四题型问卷先提交真实答卷；截止或关闭；另一账号登录查看详情',expected='200含所有题目选项及校验；不含答卷身份统计或跳转规则；新提交410且原答卷仍仅一份')
def test_survey_readonly_details(client,owner,state):
    survey=create(client,owner,[q('s',title='喜欢什么',rules=[{'op':'eq','value':'b','target':'END'}]),
        q('m','multiple',title='选择水果',validation={'min_choices':1,'max_choices':2}),
        q('t','text',title='你的建议',validation={'min_length':2,'max_length':30}),
        q('n','number',title='你的年龄',validation={'minimum':0,'maximum':120,'integer_only':True})])
    publish(client,owner,survey)
    assert submit(client,owner,survey,{'s':'a','m':['b'],'t':'私密答卷内容','n':20}).status_code==201
    changes={'deadline':now()-timedelta(seconds=1)} if state=='expired' else {'status':'closed'}
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':changes})
    reader=login(client,'detailsreader')
    base='/api/public/surveys/'+survey['_id']
    response=client.get(base+'/status',headers=reader)
    assert response.status_code==200
    data=response.json()
    assert data['availability']==state and data['can_fill'] is False
    assert [q['title'] for q in data['questions']]==['喜欢什么','选择水果','你的建议','你的年龄']
    assert data['questions'][0]['options']==[{'id':'a','label':'A'},{'id':'b','label':'B'}]
    assert data['questions'][1]['validation']['max_choices']==2
    assert data['questions'][2]['validation']['max_length']==30
    assert data['questions'][3]['validation']['integer_only'] is True
    for forbidden in ['owner_id','admissions','answers','respondent_id','responses','rules','私密答卷内容']:
        assert forbidden not in response.text
    assert submit(client,reader,survey,{'s':'b'}).status_code==410
    assert client.app.state.db.responses.count_documents({})==1


@pytest.mark.case(title='空草稿带当前内容直接发布',input='创建按钮生成空草稿；不PUT保存；提交当前四题型内容直接发布',expected='一次200保存并发布；同一ID及创建时间；revision递增；生成分享链接且可填写')
def test_publish_direct(client,owner):
    draft=client.post('/api/surveys/drafts',headers=owner).json()
    payload={'title':'直接发布问卷','description':'当前编辑内容','allow_anonymous':True,'allow_multiple':False,
        'deadline':(now()+timedelta(days=1)).isoformat(),'revision':draft['revision'],
        'questions':[q('q1',rules=[{'op':'eq','value':'a','target':'q3'}]),q('q2','multiple'),q('q3','text'),q('q4','number')]}
    base='/api/surveys/'+draft['_id']
    response=client.post(base+'/publish-direct',headers=owner,json=payload)
    assert response.status_code==200,response.text
    assert response.json()=={'status':'published','revision':2,'share_url':'/survey/'+draft['_id']}
    actual=client.get(base,headers=owner).json()
    assert actual['title']==payload['title'] and actual['description']==payload['description']
    assert actual['created_at']==draft['created_at'] and actual['published_at']
    assert actual['status']=='published' and actual['revision']==2
    assert len(actual['questions'])==4
    assert actual['questions'][0]['rules']==payload['questions'][0]['rules']
    assert client.get('/api/public/surveys/'+draft['_id'],headers=owner).status_code==200
    assert client.app.state.db.surveys.count_documents({})==1


@pytest.mark.case(title='直接发布前全量校验且失败不保存',input='无题目、空白题干、空白选项、跳转缺目标、规则过多、截止缺失/过去/格式错误',expected='均422；数据库草稿内容、状态、版本和创建时间完全不变')
def test_publish_direct_invalid(client,owner):
    draft=client.post('/api/surveys/drafts',headers=owner).json()
    base='/api/surveys/'+draft['_id']
    original=client.app.state.db.surveys.find_one({'_id':ObjectId(draft['_id'])})
    payload={'title':'不能发布','deadline':(now()+timedelta(days=1)).isoformat(),'questions':[q('q1')],'revision':1}
    wrong=[{'questions':[]},{'questions':[q('q1',title=' ')]},
        {'questions':[q('q1',options=[{'id':'a','label':' '},{'id':'b','label':'B'}])]},
        {'questions':[q('q1',rules=[{'op':'eq','value':'a','target':'missing'}])]},
        {'questions':[q('q1','number',rules=[{'op':'eq','value':1,'target':'END'},{'op':'eq','value':2,'target':'END'}])]},
        {'deadline':None},{'deadline':(now()-timedelta(seconds=1)).isoformat()},{'deadline':'bad date'}]
    for overrides in wrong:
        response=client.post(base+'/publish-direct',headers=owner,json={**payload,**overrides})
        assert response.status_code==422,response.text
        assert client.app.state.db.surveys.find_one({'_id':ObjectId(draft['_id'])})==original


@pytest.mark.case(title='直接发布权限 版本和状态保护',input='未登录、他人发布、旧revision、成功后再次改内容发布、关闭后再发布',expected='401/404/409；仅有效版本一次200；已发布和关闭后的内容不被覆盖')
def test_publish_direct_guards(client,owner):
    survey=create(client,owner)
    base='/api/surveys/'+survey['_id']
    payload={k:survey[k] for k in ['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']}
    assert client.post(base+'/publish-direct',json=payload).status_code==401
    other=login(client,'directother')
    assert client.post(base+'/publish-direct',headers=other,json=payload).status_code==404
    saved=client.put(base,headers=owner,json=payload).json()
    assert client.post(base+'/publish-direct',headers=owner,json=payload).status_code==409
    payload['revision']=saved['revision']
    assert client.post(base+'/publish-direct',headers=owner,json=payload).status_code==200
    published=client.get(base,headers=owner).json()
    payload.update(revision=published['revision'],title='不得覆盖')
    assert client.post(base+'/publish-direct',headers=owner,json=payload).status_code==409
    assert client.get(base,headers=owner).json()['title']==published['title']
    assert client.post(base+'/close',headers=owner).status_code==200
    assert client.post(base+'/publish-direct',headers=owner,json=payload).status_code==409


@pytest.mark.case(title='并发直接发布只成功一次',input='同一草稿同一revision同时提交两份不同标题内容',expected='恰一200一409；最终内容与获胜请求一致；revision只增加一次')
def test_publish_direct_concurrent(client,owner):
    survey=create(client,owner)
    payload={k:survey[k] for k in ['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']}
    def send(title):
        return title,client.post('/api/surveys/'+survey['_id']+'/publish-direct',headers=owner,json={**payload,'title':title})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(send,['并发版本一','并发版本二']))
    assert sorted(response.status_code for _,response in results)==[200,409]
    winner=next(title for title,response in results if response.status_code==200)
    actual=client.get('/api/surveys/'+survey['_id'],headers=owner).json()
    assert actual['title']==winner and actual['status']=='published'
    assert actual['revision']==survey['revision']+1
