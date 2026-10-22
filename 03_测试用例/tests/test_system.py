from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo.errors import WriteError
from pymongo import MongoClient

from app.security import now
from .conftest import login


def q(qid, kind='single', **overrides):
    value = {'id': qid, 'title': qid, 'type': kind, 'required': True, 'validation': {}, 'rules': [],
             'options': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}] if kind in ('single', 'multiple') else []}
    return {**value, **overrides}


def create(client, owner, questions=None, **overrides):
    body = {'title': '测试问卷', 'deadline': (now()+timedelta(days=7)).isoformat(), 'questions': questions if questions is not None else [q('q1')], **overrides}
    response = client.post('/api/surveys', json=body, headers=owner)
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, owner, survey):
    response = client.post(f"/api/surveys/{survey['_id']}/publish", headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()['share_url'] == '/survey/' + survey['_id']


def submit(client, headers, survey, answers, anonymous=False):
    return client.post(f"/api/public/surveys/{survey['_id']}/responses", headers=headers,
                       json={'answers': answers, 'anonymous': anonymous, 'submission_id': str(uuid4())})


@pytest.mark.case(title='注册登录与会话撤销', input='重复用户名、错误密码、登录令牌、退出', expected='201/409/401；退出后令牌无效')
def test_accounts(client):
    headers = login(client, 'CaseUser')
    assert client.post('/api/auth/register', json={'username':'caseuser','password':'test-password'}).status_code == 409
    assert client.post('/api/auth/login', json={'username':'caseuser','password':'wrong-password'}).status_code == 401
    assert client.get('/api/auth/me', headers=headers).json()['username'] == 'caseuser'
    assert client.get('/api/auth/me').status_code == 401
    assert client.post('/api/auth/logout', headers=headers).status_code == 204
    assert client.get('/api/auth/me', headers=headers).status_code == 401
    stored = client.app.state.db.users.find_one()
    assert stored['password_hash'].startswith('pbkdf2_sha256$600000$')
    assert 'test-password' not in stored['password_hash']


@pytest.mark.case(title='创建 添加题目 编辑与发布', input='空草稿→添加四种题目→修改标题→发布', expected='空问卷发布422；添加201；编辑200；发布后编辑409')
def test_create_add_edit_publish(client, owner):
    survey = create(client, owner, [])
    base = '/api/surveys/' + survey['_id']
    assert client.post(base+'/publish', headers=owner).status_code == 422
    for i, kind in enumerate(('single','multiple','text','number')):
        response = client.post(base+'/questions', headers=owner, json=q('q'+str(i), kind))
        assert response.status_code == 201
        survey = response.json()
    fields = ['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']
    body = {k:survey[k] for k in fields}
    body['title'] = '修改后标题'
    edited = client.put(base, headers=owner, json=body)
    assert edited.status_code == 200
    assert client.put(base, headers=owner, json=body).status_code == 409
    publish(client, owner, survey)
    body['revision'] = edited.json()['revision']
    assert client.put(base, headers=owner, json=body).status_code == 409
    assert client.post(base+'/questions', headers=owner, json=q('q99')).status_code == 409
    assert client.get('/api/surveys', headers=owner).json()['total'] == 1


@pytest.mark.case(title='所有者隔离与登录要求', input='另一账号读取/编辑/发布/关闭/统计；未登录填写', expected='管理接口404；填写接口401；自己列表不含他人问卷')
def test_permissions(client, owner):
    survey = create(client, owner)
    other = login(client, 'other')
    base = '/api/surveys/' + survey['_id']
    for method, suffix, body in [('GET','',None),('POST','/publish',None),('POST','/close',None),
                                  ('GET','/statistics',None),('GET','/statistics/q1',None),
                                  ('PATCH','/deadline',{'deadline':(now()+timedelta(days=1)).isoformat()}),('POST','/questions',q('q2'))]:
        assert client.request(method, base+suffix, headers=other, json=body).status_code == 404
    body={k:survey[k] for k in ['title','description','allow_anonymous','allow_multiple','deadline','questions','revision']}
    assert client.put(base,headers=other,json=body).status_code == 404
    assert client.get('/api/surveys',headers=other).json()['items'] == []
    assert client.get('/api/public/surveys/'+survey['_id'],headers=other).status_code == 404
    publish(client,owner,survey)
    assert client.get('/api/public/surveys/'+survey['_id']).status_code == 401
    assert submit(client,{},survey,{'q1':'a'},True).status_code == 401
    assert client.get('/api/surveys/bad-id',headers=owner).status_code == 404


@pytest.mark.case(title='单选 多选 数字跳转与结束', input='q1=a跳q3；q3包含a,b跳q5；q5>=18结束；q1=b顺序进入q2', expected='路径按配置生效；跳过必答题无需填；夹带被跳过答案422')
def test_branching(client, owner):
    questions = [q('q1',rules=[{'op':'eq','value':'a','target':'q3'}]), q('q2','text'),
        q('q3','multiple',rules=[{'op':'contains_all','value':['a','b'],'target':'q5'}]),q('q4','text'),
        q('q5','number',rules=[{'op':'gte','value':18,'target':'END'}]),q('q6','text')]
    survey=create(client,owner,questions)
    publish(client,owner,survey)
    base='/api/public/surveys/'+survey['_id']
    assert client.post(base+'/next',headers=owner,json={'answers':{}}).json()['next_question_id']=='q1'
    assert client.post(base+'/next',headers=owner,json={'answers':{'q1':'a'}}).json()['next_question_id']=='q3'
    assert client.post(base+'/next',headers=owner,json={'answers':{'q1':'b'}}).json()['next_question_id']=='q2'
    answers={'q1':'a','q3':['a','b'],'q5':18}
    progress=client.post(base+'/next',headers=owner,json={'answers':answers}).json()
    assert progress['path']==['q1','q3','q5'] and progress['complete']
    assert submit(client,owner,survey,answers).status_code==201
    assert submit(client,owner,survey,{**answers,'q2':'偷填'}).status_code==422
    assert submit(client,owner,survey,{'q1':'a','q3':['a'],'q5':18}).status_code==422
    assert submit(client,owner,survey,{**answers,'unknown':'x'}).status_code==422


@pytest.mark.parametrize('question,value,message',[
    (q('q1'),None,'必答'), (q('q1'),'z','有效选项'),(q('q1'),['a'],'有效选项'),
    (q('q1','multiple'),['a','a'],'重复'),(q('q1','multiple'),'a','数组'),
    (q('q1','multiple',validation={'min_choices':2,'max_choices':2}),['a'],'请选择 2'),
    (q('q1','multiple',options=[{'id':x,'label':x} for x in 'abc'],validation={'max_choices':2}),['a','b','c'],'请选择'),
    (q('q1','text',validation={'min_length':2,'max_length':4}),'a','文本长度'),
    (q('q1','text',validation={'max_length':2}),'abc','文本长度'),
    (q('q1','text'),123,'字符串'),(q('q1','text'),'   ','必答'),
    (q('q1','number',validation={'integer_only':True}),1.5,'整数'),
    (q('q1','number',validation={'minimum':0}),-1,'小于'),
    (q('q1','number',validation={'maximum':120}),121,'大于'),
    (q('q1','number'),'18','有限数字'),(q('q1','number'),True,'有限数字'),
    (q('q1','number'),9_000_000_000_000_001,'有限数字'),
])
@pytest.mark.case(title='配置驱动非法答案校验',input='参数化非法答案；详见实际API轨迹',expected='422，返回题目ID及具体错误；MongoDB不新增答卷')
def test_invalid_answers(client,owner,question,value,message):
    survey=create(client,owner,[question]);publish(client,owner,survey)
    response=submit(client,owner,survey,{'q1':value})
    assert response.status_code==422
    detail=response.json()['detail']['errors'][0]
    assert detail['question_id']=='q1' and message in detail['message']
    assert client.app.state.db.responses.count_documents({})==0


@pytest.mark.case(title='合法边界与可选题',input='数字0/120、精确两选、两字文本、跳过可选题',expected='201；省略可选答案不进入统计分母')
def test_valid_boundaries(client,owner):
    survey=create(client,owner,[q('n','number',validation={'minimum':0,'maximum':120,'integer_only':True}),
        q('m','multiple',validation={'min_choices':2,'max_choices':2}),q('t','text',validation={'min_length':2,'max_length':2}),
        q('opt','multiple',required=False,validation={'min_choices':2}),q('opt2','text',required=False)],allow_multiple=True)
    publish(client,owner,survey)
    for n in [0,120]:
        assert submit(client,owner,survey,{'n':n,'m':['a','b'],'t':'你好'}).status_code==201
    stats=client.get('/api/surveys/'+survey['_id']+'/statistics',headers=owner).json()
    assert stats['response_count']==2 and stats['questions'][0]['average']==60
    assert stats['questions'][3]['answer_count']==0


@pytest.mark.parametrize('questions',[
    [q('q1'),q('q1')], [q('q1',rules=[{'op':'eq','value':'a','target':'missing'}])],
    [q('q1',rules=[{'op':'eq','value':'a','target':'q1'}])],
    [q('q1'),q('q2',rules=[{'op':'eq','value':'a','target':'q1'}])],
    [q('q1',rules=[{'op':'gt','value':5,'target':'END'}])],
    [q('q1',options=[{'id':'a','label':'A'},{'id':'a','label':'B'}])],
    [q('q1','multiple',validation={'min_choices':3})],
    [q('q1','text',validation={'min_length':5,'max_length':2})],
    [q('q1','number',validation={'minimum':5,'maximum':2})],
    [q('q1','number',validation={'minimum':.1,'maximum':.9,'integer_only':True})],
    [q('q1','text',rules=[{'op':'eq','value':'a','target':'END'}])],
    [q('q1','number',rules=[{'op':'between','value':[10,0],'target':'END'}])],
    [q('q1','multiple',rules=[{'op':'contains_all','value':[],'target':'END'}])],
    [q('q1','text',validation={'integer_only':True})],
])
@pytest.mark.case(title='非法题目和跳转配置',input='重复ID、缺失/向后循环目标、非法范围和题型规则',expected='422，在保存前拒绝配置')
def test_invalid_configuration(client,owner,questions):
    assert client.post('/api/surveys',headers=owner,json={'title':'非法配置','deadline':(now()+timedelta(days=7)).isoformat(),'questions':questions}).status_code==422
    assert client.app.state.db.surveys.count_documents({})==0


@pytest.mark.case(title='匿名与重复提交策略',input='匿名开关；同一账号实名和匿名提交；允许多次',expected='匿名不存respondent_id；禁止重复时身份切换仍409；允许重复时均201')
def test_anonymous_and_repeat(client,owner):
    denied=create(client,owner);publish(client,owner,denied)
    assert submit(client,owner,denied,{'q1':'a'},True).status_code==422
    single=create(client,owner,allow_anonymous=True,allow_multiple=False);publish(client,owner,single)
    assert submit(client,owner,single,{'q1':'a'},True).status_code==201
    assert submit(client,owner,single,{'q1':'b'},False).status_code==409
    uppercase={**single,'_id':single['_id'].upper()}
    assert submit(client,owner,uppercase,{'q1':'b'},False).status_code==409
    doc=client.app.state.db.responses.find_one({'survey_id':ObjectId(single['_id'])})
    assert 'respondent_id' not in doc and len(doc['respondent_key'])==64
    another=login(client,'visitor')
    assert submit(client,another,single,{'q1':'b'},False).status_code==201
    multi=create(client,owner,allow_anonymous=True,allow_multiple=True);publish(client,owner,multi)
    for _ in range(2):assert submit(client,owner,multi,{'q1':'a'},True).status_code==201
    for doc in client.app.state.db.responses.find({'survey_id':ObjectId(multi['_id'])}):
        assert 'respondent_id' not in doc and 'respondent_key' not in doc


@pytest.mark.case(title='并发填写与唯一索引',input='8个同账号并发单次提交；8个不同账号并发填写',expected='单次策略恰好1个201和7个409；不同账号8个201')
def test_concurrency(client,owner):
    survey=create(client,owner,allow_multiple=False);publish(client,owner,survey)
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes=list(pool.map(lambda _:submit(client,owner,survey,{'q1':'a'}).status_code,range(8)))
    assert codes.count(201)==1 and codes.count(409)==7
    visitors=[login(client,'visitor'+str(i)) for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes=list(pool.map(lambda h:submit(client,h,survey,{'q1':'b'}).status_code,visitors))
    assert codes==[201]*8
    assert client.app.state.db.responses.count_documents({'survey_id':ObjectId(survey['_id'])})==9


@pytest.mark.case(title='整卷和单题统计',input='2份全答+1份只答单选；单选a,a,b；多选a/b,a；数字10,20',expected='答卷3；单选a2/b1；多选a2/b1；文本全部；数字平均15；零回答null')
def test_statistics(client,owner):
    survey=create(client,owner,[q('s'),q('m','multiple',required=False),q('t','text',required=False),q('n','number',required=False),q('empty','number',required=False)],allow_multiple=True)
    publish(client,owner,survey)
    rows=[{'s':'a','m':['a','b'],'t':'第一条','n':10},{'s':'a','m':['a'],'t':'第二条','n':20},{'s':'b'}]
    for row in rows:assert submit(client,owner,survey,row).status_code==201
    base='/api/surveys/'+survey['_id']+'/statistics'
    data=client.get(base,headers=owner).json();assert data['response_count']==3
    a,b,t,n,empty=data['questions']
    assert a['answer_count']==3 and [o['count'] for o in a['options']]==[2,1]
    assert b['answer_count']==2 and [o['count'] for o in b['options']]==[2,1]
    assert t['contents']==['第一条','第二条']
    assert n['contents']==[10,20] and n['average']==15
    assert empty['average'] is None
    assert client.get(base+'/n',headers=owner).json()==n
    assert client.get(base+'/missing',headers=owner).status_code==404


@pytest.mark.case(title='截止与关闭',input='过期时间发布、无时区时间、修改未来截止、模拟截止、手动关闭',expected='过去时间/无时区422；截止或关闭后读取/提交410；关闭后不能改截止')
def test_deadline_close(client,owner):
    assert client.post('/api/surveys',headers=owner,json={'title':'expired','deadline':(now()-timedelta(seconds=1)).isoformat()}).status_code==422
    survey=create(client,owner)
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':now()-timedelta(seconds=1)}})
    base='/api/surveys/'+survey['_id']
    assert client.post(base+'/publish',headers=owner).status_code==422
    assert client.patch(base+'/deadline',headers=owner,json={'deadline':'2030-01-01T00:00:00'}).status_code==422
    assert client.patch(base+'/deadline',headers=owner,json={'deadline':(now()+timedelta(days=1)).isoformat()}).status_code==200
    publish(client,owner,survey)
    assert client.get('/api/public/surveys/'+survey['_id'],headers=owner).status_code==200
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':now()-timedelta(seconds=1)}})
    assert submit(client,owner,survey,{'q1':'a'}).status_code==410
    assert client.get('/api/public/surveys/'+survey['_id'],headers=owner).status_code==410
    assert client.patch(base+'/deadline',headers=owner,json={'deadline':(now()+timedelta(days=1)).isoformat()}).status_code==200
    assert client.post(base+'/close',headers=owner).status_code==200
    assert submit(client,owner,survey,{'q1':'a'}).status_code==410
    assert client.patch(base+'/deadline',headers=owner,json={'deadline':(now()+timedelta(days=1)).isoformat()}).status_code==409
    assert client.post(base+'/publish',headers=owner).status_code==409


@pytest.mark.case(title='会话过期与数据库约束',input='会话过期；直接写入非法用户文档；检查索引',expected='过期即401不依赖TTL；MongoDB拒绝无效文档；存在部分唯一索引')
def test_database_contract(client,owner):
    db=client.app.state.db
    db.sessions.update_many({}, {'$set':{'expires_at':now()-timedelta(seconds=1)}})
    assert client.get('/api/auth/me',headers=owner).status_code==401
    with pytest.raises(WriteError):db.users.insert_one({'username':'bad'})
    indexes=list(db.responses.list_indexes())
    assert any(i.get('unique') and i.get('partialFilterExpression') for i in indexes)


@pytest.mark.case(title='基础页面与OpenAPI',input='首页、分享页、JS/CSS和OpenAPI',expected='页面和静态资源200；API schema包含提交与统计接口')
def test_pages_and_openapi(client):
    for path in ['/','/survey/'+str(ObjectId()),'/static/app.js','/static/style.css','/docs']:
        assert client.get(path).status_code==200
    schema=client.get('/openapi.json').json()
    assert '/api/public/surveys/{survey_id}/responses' in schema['paths']
    assert '/api/surveys/{survey_id}/statistics/{question_id}' in schema['paths']
    assert client.get('/api/health').json()=={'status':'ok'}


@pytest.mark.case(title='非法请求与非有限数字',input='畸形JSON、数字NaN、配置Infinity、对象用户名、未知字段',expected='均422且不回显密码，不会变成500')
def test_malformed_requests(client,owner):
    survey=create(client,owner,[q('q1','number')]);publish(client,owner,survey)
    response=client.post('/api/public/surveys/'+survey['_id']+'/responses',headers={**owner,'Content-Type':'application/json'},
        content='{"submission_id":"'+str(uuid4())+'","answers":{"q1":NaN}}')
    assert response.status_code==422 and '有限数字' in response.text
    response=client.post('/api/surveys',headers={**owner,'Content-Type':'application/json'},
        content='{"title":"x","questions":[{"id":"q1","title":"x","type":"number","validation":{"minimum":Infinity}}]}')
    assert response.status_code==422
    assert client.post('/api/surveys',headers={**owner,'Content-Type':'application/json'},content='{').status_code==422
    response=client.post('/api/auth/login',json={'username':{'$ne':None},'password':'secret-should-not-echo'})
    assert response.status_code==422 and 'secret-should-not-echo' not in response.text
    assert client.post('/api/surveys',headers=owner,json={'title':'x','owner_id':'injected'}).status_code==422


@pytest.mark.case(title='真实连接异常处理',input='将健康检查连接临时指向不可用本地MongoDB端口',expected='503；返回可理解消息且不泄漏连接细节')
def test_unavailable_database(client):
    import socket
    with socket.socket() as guard:
        guard.bind(('127.0.0.1',0))
        port=guard.getsockname()[1]
        unavailable=MongoClient(f'mongodb://127.0.0.1:{port}',serverSelectionTimeoutMS=150,connectTimeoutMS=150)
        original=client.app.state.db
        try:
            client.app.state.db=unavailable['test_unavailable']
            response=client.get('/api/health')
            assert response.status_code==503
            assert response.json()=={'detail':'数据库暂时不可用，请稍后重试'}
        finally:
            client.app.state.db=original
            unavailable.close()
