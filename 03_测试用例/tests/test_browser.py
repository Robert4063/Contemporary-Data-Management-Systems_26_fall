"""Real Chromium UI workflow; requires installed Chrome or PLAYWRIGHT_CHROMIUM_EXECUTABLE."""
import os
from pathlib import Path
from datetime import datetime, timedelta
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright, expect

TEST_ROOT = Path(__file__).resolve().parents[1]
ROOT = TEST_ROOT.parent / '01_代码'
OUTPUT = TEST_ROOT / '运行结果'
OUTPUT.mkdir(exist_ok=True)


@pytest.fixture
def browser_url(client):
    settings=client.app.state.settings
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'MONGO_URI':settings.mongo_uri,'MONGO_DB':settings.mongo_db,'APP_SECRET':settings.app_secret}
    log=(OUTPUT/'browser-server.log').open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--app-dir','backend','--host','127.0.0.1','--port',str(port)],
        cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(80):
            try:
                with urlopen(base+'/api/health',timeout=1) as response:
                    if response.status==200:break
            except (URLError,ConnectionError):time.sleep(.1)
        else:pytest.fail('UI测试服务器启动失败；见运行结果/browser-server.log')
        yield base
    finally:
        process.terminate();process.wait(timeout=20);log.close()


@pytest.mark.case(title='同站点标签页共用登录且退出同步',input='先打开问卷登录页；另一标签页登录；独立新标签打开可填及截止问卷；退出后刷新',expected='登录仅一次；所有同站点标签免重复登录；后端me检查有效；截止仍只读；退出后各页要求登录且暂存清除')
def test_browser_shared_login(browser_url,client):
    from bson import ObjectId
    from app.security import now
    from .conftest import login
    from .test_system import create,publish
    headers=login(client,'sharedowner')
    survey=create(client,headers);publish(client,headers,survey)
    expired=create(client,headers);publish(client,headers,expired)
    client.app.state.db.surveys.update_one({'_id':ObjectId(expired['_id'])},{'$set':{'deadline':now()-timedelta(seconds=1)}})
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        context=browser.new_context()
        logins=[];errors=[]
        context.on('request',lambda request:logins.append(request.url) if request.method=='POST' and request.url.endswith('/api/auth/login') else None)
        context.on('page',lambda page:page.on('pageerror',lambda error:errors.append(str(error))))
        waiting=context.new_page()
        waiting.goto(browser_url+'/survey/'+survey['_id'])
        expect(waiting.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        home=context.new_page();home.goto(browser_url)
        home.get_by_label('用户名',exact=True).fill('sharedowner')
        home.get_by_label('密码',exact=True).fill('test-password-123')
        home.get_by_role('button',name='登录',exact=True).click()
        expect(home.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        expect(waiting.get_by_role('heading',name='q1 *',exact=True)).to_be_visible()
        independent=context.new_page();independent.goto(browser_url+'/survey/'+survey['_id'])
        expect(independent.get_by_role('heading',name='q1 *',exact=True)).to_be_visible()
        expect(independent.get_by_role('heading',name='登录账号',exact=True)).to_have_count(0)
        assert independent.evaluate("sessionStorage.getItem('survey_token')")==None
        independent.get_by_label('A. A',exact=True).check()
        independent.reload()
        expect(independent.get_by_label('A. A',exact=True)).to_be_checked()
        ended=context.new_page();ended.goto(browser_url+'/survey/'+expired['_id'])
        expect(ended.get_by_role('heading',name='问卷已截止',exact=True)).to_be_visible()
        assert len(logins)==1
        # A separate browser context has no shared origin storage and must log in.
        separate=browser.new_context()
        guest=separate.new_page();guest.goto(browser_url+'/survey/'+survey['_id'])
        expect(guest.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        separate.close()
        home.get_by_role('button',name='退出登录',exact=True).click()
        for page in [home,waiting,independent,ended]:
            expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
            expect(page.locator('.question,.survey-detail')).to_have_count(0)
        assert home.evaluate("localStorage.getItem('survey_token')")==None
        assert home.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('survey_draft:')).length")==0
        independent.reload()
        expect(independent.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        assert len(logins)==1 and errors==[]
        browser.close()


@pytest.mark.case(title='旧请求401不能清除新登录',input='暂挂旧会话下一步请求；另一标签退出后重新登录；再返回旧请求401',expected='保留新会话与填写页面；旧响应不清除新登录；新会话仍可正常下一步')
def test_browser_stale_session_response(browser_url,client):
    from .conftest import login
    from .test_system import create,publish
    headers=login(client,'stalesession')
    survey=create(client,headers);publish(client,headers,survey)
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        context=browser.new_context();home=context.new_page();home.goto(browser_url)
        def sign_in():
            home.get_by_label('用户名',exact=True).fill('stalesession')
            home.get_by_label('密码',exact=True).fill('test-password-123')
            home.get_by_role('button',name='登录',exact=True).click()
            expect(home.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        sign_in()
        page=context.new_page();page.goto(browser_url+'/survey/'+survey['_id'])
        page.get_by_label('A. A',exact=True).check()
        pending=[];pattern='**/api/public/surveys/*/next'
        page.route(pattern,lambda route:pending.append(route))
        button=page.get_by_role('button',name='下一步',exact=True).element_handle()
        with page.expect_request(pattern):
            page.get_by_role('button',name='下一步',exact=True).click()
        assert len(pending)==1
        home.get_by_role('button',name='退出登录',exact=True).click()
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        sign_in()
        expect(page.get_by_role('heading',name='q1 *',exact=True)).to_be_visible()
        with page.expect_response(pattern):
            pending[0].fulfill(status=401,json={'detail':'登录已过期或无效'})
        page.wait_for_function('button=>!button.disabled',arg=button)
        expect(page.get_by_role('heading',name='q1 *',exact=True)).to_be_visible(timeout=3000)
        expect(home.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        page.unroute(pattern)
        page.get_by_label('A. A',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='确认提交',exact=True)).to_be_visible()
        browser.close()


@pytest.mark.case(title='旧标签页登录状态迁移',input='仅sessionStorage存在旧有效token；刷新后独立新标签访问问卷',expected='后端验证后自动登录；迁移到共享存储并清理旧值；新标签无须登录')
def test_browser_migrate_login(browser_url,client):
    from .conftest import login
    from .test_system import create,publish
    headers=login(client,'legacylogin')
    survey=create(client,headers);publish(client,headers,survey)
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        context=browser.new_context();page=context.new_page();page.goto(browser_url)
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        old_token=headers['Authorization'].split(' ',1)[1]
        page.evaluate("value=>sessionStorage.setItem('survey_token',value)",old_token)
        page.reload()
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        assert page.evaluate("localStorage.getItem('survey_token')")==old_token
        assert page.evaluate("sessionStorage.getItem('survey_token')")==None
        other=context.new_page();other.goto(browser_url+'/survey/'+survey['_id'])
        expect(other.get_by_role('heading',name='q1 *',exact=True)).to_be_visible()
        browser.close()


@pytest.mark.parametrize('kind',['expired','invalid'])
@pytest.mark.case(title='无效共享会话不能绕过登录',input='共享存储中已过期或伪造token；打开问卷链接',expected='me返回401；清理共享token；显示登录表单且不请求问卷状态或内容')
def test_browser_invalid_shared_login(browser_url,client,kind):
    from app.security import now
    from .conftest import login
    from .test_system import create,publish
    headers=login(client,'expiredlogin')
    survey=create(client,headers);publish(client,headers,survey)
    saved=headers['Authorization'].split(' ',1)[1] if kind=='expired' else 'forged-invalid-session'
    if kind=='expired':
        client.app.state.db.sessions.update_many({}, {'$set':{'expires_at':now()-timedelta(seconds=1)}})
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page();page.goto(browser_url)
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        page.evaluate("value=>localStorage.setItem('survey_token',value)",saved)
        requests=[];checks=[]
        page.on('request',lambda request:requests.append(request.url))
        page.on('response',lambda response:checks.append(response.status) if response.url.endswith('/api/auth/me') else None)
        page.goto(browser_url+'/survey/'+survey['_id'])
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        assert 401 in checks
        assert not any('/api/public/surveys/' in url for url in requests)
        assert page.evaluate("localStorage.getItem('survey_token')")==None
        browser.close()


@pytest.mark.case(title='直接发布确认 取消 校验与原发布入口',input='新建编辑不存草稿；取消确认；确认但题目无效；改好直接发布；工作台发布取消再确认',expected='取消无发布请求且保留编辑；无效422原草稿不变；确认后一次发布生成链接；两个入口都有确认')
def test_browser_direct_publish(browser_url,client):
    from .conftest import login
    from .test_system import create
    headers=login(client,'directowner')
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page()
        errors=[];mutations=[];dialogs=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:mutations.append((request.method,request.url)) if request.method in ('POST','PUT','PATCH') else None)
        def cancel(dialog):
            dialogs.append((dialog.type,dialog.message));dialog.dismiss()
        def confirm(dialog):
            dialogs.append((dialog.type,dialog.message));dialog.accept()
        page.goto(browser_url)
        page.get_by_label('用户名',exact=True).fill('directowner')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.get_by_role('button',name='创建问卷',exact=True).click()
        page.get_by_label('问卷标题',exact=True).fill('不手动保存直接发布')
        page.get_by_label('问卷说明',exact=True).fill('发布当前编辑的内容')
        page.get_by_label('截止时间（必填，本地时间）',exact=True).fill((datetime.now()+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'))
        page.get_by_role('button',name='添加题目',exact=True).click()
        original=client.app.state.db.surveys.find_one()
        sid=str(original['_id'])
        assert original['questions']==[] and original['title']=='未命名问卷'
        page.once('dialog',cancel)
        page.get_by_role('button',name='直接发布',exact=True).click()
        expect(page.get_by_label('问卷标题',exact=True)).to_have_value('不手动保存直接发布')
        assert not any('/publish' in url for _,url in mutations)
        assert client.app.state.db.surveys.find_one()==original
        page.once('dialog',confirm)
        page.get_by_role('button',name='直接发布',exact=True).click()
        expect(page.get_by_role('alert')).not_to_be_empty()
        expect(page.get_by_role('heading',name='问卷设置',exact=True)).to_be_visible()
        assert client.app.state.db.surveys.find_one()==original
        page.get_by_label('题目标题',exact=True).fill('你的选择')
        page.once('dialog',confirm)
        page.get_by_role('button',name='直接发布',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('发布成功')
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        expect(page.locator('a[href="/survey/'+sid+'"]')).to_be_visible()
        published=client.app.state.db.surveys.find_one()
        assert published['status']=='published' and published['revision']==2
        assert published['title']=='不手动保存直接发布' and published['questions'][0]['title']=='你的选择'
        assert published['created_at']==original['created_at']
        assert not any(method=='PUT' and url.endswith('/api/surveys/'+sid) for method,url in mutations)
        assert sum(url.endswith('/publish-direct') for _,url in mutations)==2
        second=create(client,headers,title='先存草稿再发布')
        page.reload()
        expect(page.get_by_role('button',name='发布',exact=True)).to_be_visible()
        page.once('dialog',cancel)
        page.get_by_role('button',name='发布',exact=True).click()
        assert client.get('/api/surveys/'+second['_id'],headers=headers).json()['status']=='draft'
        assert not any(url.endswith('/'+second['_id']+'/publish') for _,url in mutations)
        page.once('dialog',confirm)
        page.get_by_role('button',name='发布',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('发布成功')
        assert client.get('/api/surveys/'+second['_id'],headers=headers).json()['status']=='published'
        assert len(dialogs)==5 and all(kind=='confirm' for kind,_ in dialogs)
        assert all('确定发布问卷' in message and '无法修改' in message for _,message in dialogs)
        assert errors==[]
        browser.close()


@pytest.mark.parametrize('state,heading',[
    ('expired','问卷已截止'),('closed','问卷已关闭'),('incomplete','问卷暂不可填写')])
@pytest.mark.case(title='登录后进入不可填写状态页且可返回',input='未登录访问截止/关闭/缺截止链接；登录；查看状态和只读题目；返回工作台并刷新',expected='登录前无状态信息或请求；登录后显示题目选项和截止状态无答题控件；返回真实根路径且保持登录；无重复登录死局')
def test_browser_unavailable_survey(browser_url,client,state,heading):
    from bson import ObjectId
    from app.security import now
    from .conftest import login
    from .test_system import create,publish
    headers=login(client,'stateowner')
    survey=create(client,headers,title='状态页面测试',description='已登录仍可进入查看状态')
    publish(client,headers,survey)
    changes={'expired':{'deadline':now()-timedelta(seconds=1)},
        'closed':{'status':'closed'},'incomplete':{'deadline':None}}[state]
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':changes})
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page(viewport={'width':1100,'height':800})
        errors=[];requests=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:requests.append(request.url))
        page.goto(browser_url+'/survey/'+survey['_id'])
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        expect(page.get_by_text('状态页面测试',exact=True)).to_have_count(0)
        assert not any('/api/public/surveys/' in url for url in requests)
        expect(page.get_by_role('link',name='返回工作台',exact=True)).to_be_visible()
        page.get_by_label('用户名',exact=True).fill('stateowner')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        expect(page.get_by_role('heading',name=heading,exact=True)).to_be_visible()
        expect(page.get_by_role('heading',name='状态页面测试',exact=True)).to_be_visible()
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_have_count(0)
        expect(page.get_by_role('heading',name='第 1 题：q1',exact=True)).to_be_visible()
        expect(page.get_by_text('A. A',exact=True)).to_be_visible()
        expect(page.locator('#app input,#app textarea,#app select')).to_have_count(0)
        expect(page.locator('.question')).to_have_count(0)
        expect(page.get_by_role('button',name='提交答卷',exact=True)).to_have_count(0)
        assert not any(url.endswith('/api/public/surveys/'+survey['_id']) for url in requests)
        if state=='expired':page.screenshot(path=str(OUTPUT/'expired-survey.png'),full_page=True)
        page.get_by_role('link',name='返回工作台',exact=True).click()
        expect(page).to_have_url(browser_url+'/')
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        assert client.app.state.db.responses.count_documents({})==0
        assert errors==[]
        browser.close()


@pytest.mark.case(title='截止后四题型只读详情及旧后端错误说明',input='四题型问卷截止；登录查看全部题目选项校验；模拟旧后端status路由Not Found',expected='全部题目按顺序展示；A/B选项和各限制正确；无填写控件；旧后端提示重启而非裸Not Found；可返回工作台')
def test_browser_readonly_survey_details(browser_url,client):
    from bson import ObjectId
    from app.security import now
    from .conftest import login
    from .test_system import create,publish,q
    headers=login(client,'readonlyowner')
    survey=create(client,headers,[q('s',title='喜欢的颜色',options=[{'id':'red','label':'红色'},{'id':'blue','label':'蓝色'}]),
        q('m','multiple',title='喜欢的水果',options=[{'id':'apple','label':'苹果'},{'id':'pear','label':'梨'}],validation={'min_choices':1,'max_choices':2}),
        q('t','text',title='写下建议',required=False,validation={'min_length':2,'max_length':30}),
        q('n','number',title='年龄',validation={'minimum':0,'maximum':120,'integer_only':True})],title='截止后查看问卷内容')
    publish(client,headers,survey)
    client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':now()-timedelta(seconds=1)}})
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page(viewport={'width':1100,'height':850})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(browser_url+'/survey/'+survey['_id'])
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        expect(page.get_by_text('喜欢的颜色',exact=True)).to_have_count(0)
        page.get_by_label('用户名',exact=True).fill('readonlyowner')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        expect(page.get_by_role('heading',name='问卷已截止',exact=True)).to_be_visible()
        expect(page.get_by_role('heading',name='问卷题目（只读，共 4 题）',exact=True)).to_be_visible()
        for title in ['第 1 题：喜欢的颜色','第 2 题：喜欢的水果','第 3 题：写下建议','第 4 题：年龄']:
            expect(page.get_by_role('heading',name=title,exact=True)).to_be_visible()
        for text in ['A. 红色','B. 蓝色','A. 苹果','B. 梨','选择数量：1–2 项','文本长度：2–30 个字符','数字范围：0 至 120；只允许整数','文本填空 · 选答']:
            expect(page.get_by_text(text,exact=True)).to_be_visible()
        expect(page.locator('#app input,#app textarea,#app select')).to_have_count(0)
        expect(page.get_by_role('button',name='下一步',exact=True)).to_have_count(0)
        expect(page.get_by_role('button',name='提交答卷',exact=True)).to_have_count(0)
        page.screenshot(path=str(OUTPUT/'expired-survey-details.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        pattern='**/api/public/surveys/*/status'
        page.route(pattern,lambda route:route.fulfill(status=404,json={'detail':'Not Found'}))
        page.reload()
        expect(page.get_by_role('heading',name='问卷暂不可访问',exact=True)).to_be_visible()
        expect(page.get_by_text('当前后端未提供问卷状态接口，请从项目目录重启后端后再试。',exact=True)).to_be_visible()
        page.get_by_role('link',name='返回工作台',exact=True).click()
        expect(page).to_have_url(browser_url+'/')
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        assert client.app.state.db.responses.count_documents({})==0
        assert errors==[]
        browser.close()


@pytest.mark.case(title='填写中截止 下一步提交及自动到期均可退出',input='先正常登录填选项；下一步前截止；恢复后提交前截止；恢复后浏览器时钟到期',expected='三种情况均转状态页并移除答题控件；草稿保留；无答卷入库；能返回已登录工作台')
def test_browser_deadline_during_fill(browser_url,client):
    from bson import ObjectId
    from app.security import now
    from .conftest import login
    from .test_system import create,publish
    headers=login(client,'duringowner')
    survey=create(client,headers,deadline=(now()+timedelta(minutes=5)).isoformat())
    publish(client,headers,survey)
    def deadline(value):
        client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':value}})
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page()
        page.clock.install()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(browser_url+'/survey/'+survey['_id'])
        page.get_by_label('用户名',exact=True).fill('duringowner')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.get_by_label('A. A',exact=True).check()
        deadline(now()-timedelta(seconds=1))
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='问卷已截止',exact=True)).to_be_visible()
        expect(page.locator('.question')).to_have_count(0)
        deadline(now()+timedelta(minutes=5))
        page.get_by_role('button',name='重新检查状态',exact=True).click()
        expect(page.get_by_label('A. A',exact=True)).to_be_checked()
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='确认提交',exact=True)).to_be_visible()
        deadline(now()-timedelta(seconds=1))
        page.get_by_role('button',name='提交答卷',exact=True).click()
        expect(page.get_by_role('heading',name='问卷已截止',exact=True)).to_be_visible()
        assert client.app.state.db.responses.count_documents({})==0
        assert page.evaluate("Object.keys(localStorage).some(k=>k.startsWith('survey_draft:'))")
        deadline(now()+timedelta(minutes=5))
        page.get_by_role('button',name='重新检查状态',exact=True).click()
        expect(page.get_by_role('heading',name='确认提交',exact=True)).to_be_visible()
        page.get_by_role('button',name='清除进度并重新填写',exact=True).click()
        page.get_by_label('B. B',exact=True).check()
        deadline(now()-timedelta(seconds=1))
        page.clock.fast_forward(6*60*1000)
        expect(page.get_by_role('heading',name='问卷已截止',exact=True)).to_be_visible()
        expect(page.locator('.question')).to_have_count(0)
        page.get_by_role('link',name='返回工作台',exact=True).click()
        expect(page).to_have_url(browser_url+'/')
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        assert client.app.state.db.responses.count_documents({})==0
        assert errors==[]
        browser.close()


@pytest.mark.case(title='登录期间截止和无效链接有返回入口',input='打开仍有效问卷的登录页；登录前使其截止；登录后返回；再访问不存在的ID',expected='登录后直接显示截止页；无效链接显示错误页且无登录死局；两者均可返回工作台')
def test_browser_login_expiry_and_missing(browser_url,client):
    from bson import ObjectId
    from app.security import now
    from .conftest import login
    from .test_system import create,publish
    headers=login(client,'loginexpiry')
    survey=create(client,headers);publish(client,headers,survey)
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page()
        page.goto(browser_url+'/survey/'+survey['_id'])
        expect(page.get_by_role('heading',name='登录账号',exact=True)).to_be_visible()
        client.app.state.db.surveys.update_one({'_id':ObjectId(survey['_id'])},{'$set':{'deadline':now()-timedelta(seconds=1)}})
        page.get_by_label('用户名',exact=True).fill('loginexpiry')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        expect(page.get_by_role('heading',name='问卷已截止',exact=True)).to_be_visible()
        page.get_by_role('link',name='返回工作台',exact=True).click()
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        page.goto(browser_url+'/survey/'+str(ObjectId()))
        expect(page.get_by_role('heading',name='问卷暂不可访问',exact=True)).to_be_visible()
        page.get_by_role('link',name='返回工作台',exact=True).click()
        expect(page).to_have_url(browser_url+'/')
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        browser.close()


@pytest.mark.case(title='真实浏览器端到端', input='页面注册登录、四种题目、可视化规则、发布、匿名/实名两份答卷、数字错误、返回修改、统计和关闭',
    expected='UI无脚本错误；跳转跳过q2；非法小数被拒绝；两份答卷平均21；关闭后提示不可填写')
def test_browser_workflow(browser_url,client,request):
    errors=[]
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900})
        def trace(response):
            if '/api/' not in response.url:return
            body=response.request.post_data_json if response.request.post_data else None
            if isinstance(body,dict) and 'password' in body:body={**body,'password':'<redacted>'}
            output=response.json() if response.status!=204 else None
            if isinstance(output,dict) and 'access_token' in output:output={**output,'access_token':'<redacted>'}
            request.node.api_trace.append({'method':response.request.method,'path':response.url.split(browser_url)[-1],
                'input':body,'status':response.status,'actual_output':output})
        page.on('response',trace)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(browser_url)
        page.get_by_role('button',name='没有账号，去注册').click()
        page.get_by_label('用户名',exact=True).fill('browserowner')
        page.get_by_label('密码',exact=True).fill('browser-password-123')
        page.get_by_role('button',name='注册',exact=True).click()
        expect(page.get_by_role('heading',name='登录账号')).to_be_visible()
        page.get_by_label('用户名',exact=True).fill('browserowner')
        page.get_by_label('密码',exact=True).fill('browser-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.get_by_role('button',name='创建问卷',exact=True).click()
        expect(page.get_by_text('创建时间：',exact=False)).to_be_visible()
        draft_before_save=client.app.state.db.surveys.find_one()
        assert draft_before_save and draft_before_save['questions']==[]
        original_created_at=draft_before_save['created_at']
        page.get_by_label('问卷标题',exact=True).fill('浏览器完整验证')
        page.get_by_label('问卷说明',exact=True).fill('通过真实浏览器验证四种题型与跳转')
        page.get_by_label('允许匿名填写（仍须登录）',exact=True).check()
        expect(page.get_by_label('允许同一账号多次提交',exact=True)).not_to_be_checked()
        page.get_by_label('允许同一账号多次提交',exact=True).check()
        for i,kind in enumerate(['single','multiple','text','number']):
            page.get_by_role('button',name='添加题目',exact=True).click()
            card=page.locator('.question').nth(i)
            card.get_by_label('题型',exact=True).select_option(kind)
            card=page.locator('.question').nth(i)
            card.get_by_label('题目标题',exact=True).fill(['分支选择','选择水果','留言','年龄'][i])
        first=page.locator('.question').nth(0)
        expect(first.locator('.single-jump')).to_have_count(0)
        first.get_by_label('启用选项跳转',exact=True).check()
        expect(first.locator('.single-jump')).to_have_count(2)
        first.get_by_label('选项 A「选项一」跳转到',exact=True).select_option('q3')
        expect(first.get_by_label('选项 B「选项二」跳转到',exact=True)).to_have_value('')
        multi=page.locator('.question').nth(1)
        multi.get_by_label('最少选择数（可不答题仅在作答时检查）',exact=True).fill('2')
        multi.get_by_label('最多选择数（留空为全部）',exact=True).fill('2')
        text=page.locator('.question').nth(2)
        text.get_by_label('最短文本长度',exact=True).fill('2')
        text.get_by_label('最长文本长度（默认 10000）',exact=True).fill('4')
        number=page.locator('.question').nth(3)
        number.get_by_label('数字最小值（可留空）',exact=True).fill('0')
        number.get_by_label('数字最大值（可留空）',exact=True).fill('120')
        number.get_by_label('只允许整数',exact=True).check()
        for card in [multi,number]:
            expect(card.get_by_role('button',name='添加跳转规则',exact=True)).to_have_count(0)
            card.get_by_label('启用条件跳转',exact=True).check()
            add=card.get_by_role('button',name='添加跳转规则',exact=True)
            expect(add).to_be_enabled()
            add.click()
            expect(card.locator('.rule')).to_have_count(1)
            expect(card.get_by_role('button',name='添加跳转规则',exact=True)).to_be_disabled()
            card.get_by_role('button',name='删除规则',exact=True).click()
            expect(card.locator('.rule')).to_have_count(0)
            expect(card.get_by_role('button',name='添加跳转规则',exact=True)).to_be_enabled()
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('截止时间必须填写')
        page.get_by_label('截止时间（必填，本地时间）',exact=True).fill((datetime.now()+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'))
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('草稿已保存')
        assert client.app.state.db.surveys.find_one() ['created_at']==original_created_at
        page.get_by_role('button',name='返回列表',exact=True).click()
        page.once('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='发布',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('发布成功')
        href=page.locator('a[href^="/survey/"]').get_attribute('href')
        page.goto(browser_url+href)
        page.get_by_label('匿名提交（答卷不保存账号身份）',exact=True).check()
        page.get_by_label('A. 选项一',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='留言 *',exact=True)).to_be_visible()
        page.get_by_label('填写文本',exact=True).fill('你好')
        page.reload()
        expect(page.get_by_label('填写文本',exact=True)).to_have_value('你好')
        expect(page.get_by_label('匿名提交（答卷不保存账号身份）',exact=True)).to_be_checked()
        page.get_by_role('button',name='下一步',exact=True).click()
        page.get_by_label('填写数字',exact=True).fill('1.5')
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('必须输入整数')
        page.get_by_label('填写数字',exact=True).fill('22')
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='确认提交',exact=True)).to_be_visible()
        page.get_by_role('button',name='返回修改',exact=True).click()
        expect(page.get_by_label('填写数字',exact=True)).to_have_value('22')
        page.get_by_label('填写数字',exact=True).fill('20')
        page.get_by_role('button',name='下一步',exact=True).click()
        page.get_by_role('button',name='提交答卷',exact=True).click()
        expect(page.get_by_role('heading',name='提交成功',exact=True)).to_be_visible()
        page.get_by_role('button',name='再次填写',exact=True).click()
        page.get_by_label('B. 选项二',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='选择水果 *',exact=True)).to_be_visible()
        page.get_by_label('A. 选项一',exact=True).check()
        page.get_by_label('B. 选项二',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        page.get_by_label('填写文本',exact=True).fill('再见')
        page.get_by_role('button',name='下一步',exact=True).click()
        page.get_by_label('填写数字',exact=True).fill('22')
        page.get_by_role('button',name='下一步',exact=True).click()
        page.get_by_role('button',name='提交答卷',exact=True).click()
        expect(page.get_by_role('heading',name='提交成功',exact=True)).to_be_visible()
        page.goto(browser_url)
        page.get_by_role('button',name='查看统计',exact=True).click()
        expect(page.get_by_text('已提交 2 份答卷。重复提交按答卷次数计数。',exact=True)).to_be_visible()
        expect(page.get_by_text('平均值：21',exact=True)).to_be_visible()
        page.get_by_label('统计范围',exact=True).select_option('q4')
        expect(page.get_by_text('平均值：21',exact=True)).to_be_visible()
        page.screenshot(path=str(OUTPUT/'browser-statistics.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.get_by_role('button',name='返回列表',exact=True).click()
        page.get_by_role('button',name='关闭问卷',exact=True).click()
        expect(page.get_by_text('已关闭',exact=True)).to_be_visible()
        page.goto(browser_url+href)
        expect(page.get_by_role('heading',name='问卷已关闭',exact=True)).to_be_visible()
        expect(page.get_by_role('link',name='返回工作台',exact=True)).to_be_visible()
        assert errors==[]
        assert client.app.state.db.responses.count_documents({})==2
        anonymous=client.app.state.db.responses.find_one({'anonymous':True})
        assert 'respondent_id' not in anonymous
        request.node.browser_summary={'browser_version':browser.version,'page_errors':errors,
            'saved_responses':2,'numeric_average':21,'mobile_width':390,'horizontal_overflow':False,
            'screenshot':'运行结果/browser-statistics.png','closed_survey_rejected':True}
        browser.close()


@pytest.mark.case(title='字母编号在编辑 跳转 填写 确认和统计同步',input='单选多选删除B；内容改名；关闭并恢复跳转；保存重开；正常作答与统计；检查Z之后编号',expected='A/B/C连续；规则关联原选项；关闭不跳题；所有页面字母一致且无内部ID；AA/AB等正确')
def test_browser_alphabet_and_jump_switch(browser_url,client):
    from .conftest import login
    from .test_system import create,q
    headers=login(client,'lettersowner')
    ids=['o' + str(i)*32 for i in range(1,5)]
    options=[{'id':oid,'label':label} for oid,label in zip(ids,['苹果','香蕉','西瓜','葡萄'])]
    rule={'op':'eq','value':ids[2],'target':'END'}
    survey=create(client,headers,[q('q1',options=options,jump_enabled=False,rules=[rule]),
        q('q2','multiple',options=options,jump_enabled=False)])
    sid=survey['_id']
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(browser_url)
        page.get_by_label('用户名',exact=True).fill('lettersowner')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        assert page.evaluate('[0,25,26,27,51,52,99].map(optionCode)')==['A','Z','AA','AB','AZ','BA','CV']
        first=page.locator('.question').nth(0)
        multi=page.locator('.question').nth(1)
        for card in [first,multi]:
            expect(card.get_by_label('选项 A',exact=True)).to_have_value('苹果')
            card.get_by_label('选项 B',exact=True).locator('..').locator('..').get_by_role('button',name='删除选项',exact=True).click()
            expect(card.get_by_label('选项 B',exact=True)).to_have_value('西瓜')
            expect(card.get_by_label('选项 C',exact=True)).to_have_value('葡萄')
            expect(card.get_by_label('选项 D',exact=True)).to_have_count(0)
        expect(first.locator('.single-jump')).to_have_count(0)
        first.get_by_label('启用选项跳转',exact=True).check()
        expect(first.get_by_label('选项 B「西瓜」跳转到',exact=True)).to_have_value('END')
        first.get_by_label('选项 B',exact=True).fill('甜西瓜')
        expect(first.get_by_label('选项 B「甜西瓜」跳转到',exact=True)).to_have_value('END')
        first.get_by_label('启用选项跳转',exact=True).uncheck()
        expect(first.locator('.single-jump')).to_have_count(0)
        expect(multi.get_by_role('button',name='添加跳转规则',exact=True)).to_have_count(0)
        multi.get_by_label('启用条件跳转',exact=True).check()
        multi.get_by_role('button',name='添加跳转规则',exact=True).click()
        expect(multi.get_by_label('B. 西瓜',exact=True)).to_be_visible()
        multi.get_by_label('选项 B',exact=True).fill('甜西瓜')
        expect(multi.get_by_label('B. 甜西瓜',exact=True)).to_be_visible()
        multi.get_by_label('A. 苹果',exact=True).check()
        multi.get_by_label('启用条件跳转',exact=True).uncheck()
        expect(multi.locator('.rule')).to_have_count(0)
        for oid in ids:
            assert oid not in page.locator('#app').inner_text()
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('草稿已保存')
        saved=client.get('/api/surveys/'+sid,headers=headers).json()['questions']
        assert all([o['id'] for o in question['options']]==[ids[0],ids[2],ids[3]] for question in saved)
        assert saved[0]['rules']==[rule] and saved[0]['jump_enabled'] is False
        assert saved[1]['rules'][0]['value']==[ids[0]] and saved[1]['jump_enabled'] is False
        page.reload()
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        first=page.locator('.question').nth(0)
        multi=page.locator('.question').nth(1)
        expect(first.get_by_label('启用选项跳转',exact=True)).not_to_be_checked()
        expect(first.locator('.single-jump')).to_have_count(0)
        first.get_by_label('启用选项跳转',exact=True).check()
        expect(first.get_by_label('选项 B「甜西瓜」跳转到',exact=True)).to_have_value('END')
        first.screenshot(path=str(OUTPUT/'alphabet-options.png'))
        first.get_by_label('启用选项跳转',exact=True).uncheck()
        multi.get_by_label('启用条件跳转',exact=True).check()
        expect(multi.get_by_label('A. 苹果',exact=True)).to_be_checked()
        multi.get_by_label('启用条件跳转',exact=True).uncheck()
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('草稿已保存')
        page.get_by_role('button',name='返回列表',exact=True).click()
        page.once('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='发布',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('发布成功')
        page.goto(browser_url+'/survey/'+sid)
        for label in ['A. 苹果','B. 甜西瓜','C. 葡萄']:
            expect(page.get_by_label(label,exact=True)).to_be_visible()
        page.get_by_label('B. 甜西瓜',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='q2 *',exact=True)).to_be_visible()
        page.get_by_label('B. 甜西瓜',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='确认提交',exact=True)).to_be_visible()
        expect(page.get_by_text('B. 甜西瓜',exact=True)).to_have_count(2)
        page.get_by_role('button',name='提交答卷',exact=True).click()
        expect(page.get_by_role('heading',name='提交成功',exact=True)).to_be_visible()
        page.goto(browser_url)
        page.get_by_role('button',name='查看统计',exact=True).click()
        expect(page.get_by_text('B. 甜西瓜：1 人次',exact=True)).to_be_visible()
        expect(page.get_by_text('B. 甜西瓜：1 次',exact=True)).to_be_visible()
        page.get_by_label('统计范围',exact=True).select_option('q1')
        expect(page.get_by_text('B. 甜西瓜：1 人次',exact=True)).to_be_visible()
        for oid in ids:
            assert oid not in page.locator('#app').inner_text()
        assert errors==[]
        response=client.app.state.db.responses.find_one()
        assert response['answers']=={'q1':ids[2],'q2':[ids[2]]}
        browser.close()


@pytest.mark.case(title='单选逐选项跳转与多选全都不包含',input='旧规则；选项编辑；三种题型跳转目标随标题逐字更新；空标题；旧规则选择器同步；保存重开及试跳转',expected='所有目标实时显示新标题且输入不失焦；选择和ID不变；contains_none保存执行正确')
def test_browser_option_routes(browser_url,client,request):
    from .conftest import login
    from .test_system import create,q,publish
    headers=login(client,'routesowner')
    options=[{'id':x,'label':x.upper()} for x in 'abc']
    survey=create(client,headers,[q('q1',options=options,rules=[{'op':'ne','value':'a','target':'q3'},{'op':'eq','value':'a','target':'END'}]),
        q('q2','multiple',options=options),q('q3','number',rules=[{'op':'eq','value':1,'target':'q4'}]),q('q4','text')])
    sid=survey['_id']
    from bson import ObjectId
    client.app.state.db.surveys.update_one({'_id':ObjectId(sid)},{'$set':{'questions.1.jump_enabled':True,'questions.1.rules':[
        {'op':'contains_any','value':['a'],'target':'q3'},
        {'op':'contains_none','value':['a','b'],'target':'END'}]}})
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page(viewport={'width':1280,'height':900})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(browser_url)
        page.get_by_label('用户名',exact=True).fill('routesowner')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        first=page.locator('.question').nth(0)
        expect(first.locator('.single-jump')).to_have_count(3)
        expect(first.get_by_role('button',name='添加跳转规则',exact=True)).to_have_count(0)
        expect(first.get_by_label('选项 A「A」跳转到',exact=True)).to_have_value('END')
        expect(first.get_by_label('选项 B「B」跳转到',exact=True)).to_have_value('q3')
        expect(first.get_by_label('选项 C「C」跳转到',exact=True)).to_have_value('q3')
        first.get_by_label('选项 A「A」跳转到',exact=True).select_option('')
        first.get_by_label('选项 C「C」跳转到',exact=True).select_option('q4')
        first.get_by_role('button',name='添加选项',exact=True).click()
        expect(first.locator('.single-jump')).to_have_count(4)
        added=first.locator('.single-jump').last.get_attribute('data-option-id')
        expect(first.locator('.single-jump').last.get_by_role('combobox')).to_have_value('')
        first.get_by_label('选项 D',exact=True).fill('D')
        expect(first.get_by_label('选项 D「D」跳转到',exact=True)).to_have_value('')
        first.get_by_label('选项 D',exact=True).locator('..').locator('..').get_by_role('button',name='删除选项',exact=True).click()
        expect(first.locator('.single-jump')).to_have_count(3)
        first.get_by_label('选项 C',exact=True).locator('..').locator('..').get_by_role('button',name='上移',exact=True).click()
        first.get_by_label('选项 B',exact=True).fill('西瓜')
        expect(first.get_by_label('选项 B「西瓜」跳转到',exact=True)).to_have_value('q4')
        multi=page.locator('.question').nth(1)
        expect(multi.get_by_label('选择保留的跳转规则',exact=True)).to_be_visible()
        expect(multi.locator('.rule')).to_have_count(0)
        multi.get_by_label('选择保留的跳转规则',exact=True).select_option('1')
        number=page.locator('.question').nth(2)
        number.get_by_label('题目标题',exact=True).fill('中间数字题')
        expect(multi.get_by_label('选择保留的跳转规则',exact=True)).to_have_value('1')
        expect(multi.get_by_label('选择保留的跳转规则',exact=True).locator('option').first).to_contain_text('第3题：中间数字题')
        expect(first.locator('select.jump-target option[value="q3"]')).to_have_text(['第3题：中间数字题']*3)
        multi.get_by_role('button',name='只保留所选规则',exact=True).click()
        expect(multi.locator('.rule')).to_have_count(1)
        expect(multi.get_by_label('判断条件',exact=True)).to_have_value('contains_none')
        expect(multi.get_by_role('button',name='添加跳转规则',exact=True)).to_be_disabled()
        multi.get_by_role('button',name='删除规则',exact=True).click()
        expect(multi.get_by_role('button',name='添加跳转规则',exact=True)).to_be_enabled()
        multi.get_by_role('button',name='添加跳转规则',exact=True).click()
        expect(multi.get_by_role('button',name='添加跳转规则',exact=True)).to_be_disabled()
        multi.get_by_label('判断条件',exact=True).select_option('contains_none')
        multi.get_by_label('A. A',exact=True).check()
        multi.get_by_label('B. B',exact=True).check()
        multi.get_by_label('跳转到',exact=True).select_option('q4')
        title=page.locator('.question').nth(3).get_by_label('题目标题',exact=True)
        title.fill('')
        for text in ['未命名','你','你好']:
            if text!='未命名':title.press_sequentially(text[-1])
            expect(title).to_be_focused()
            expect(first.locator('select.jump-target option[value="q4"]')).to_have_text(['第4题：'+text]*3)
            expect(multi.get_by_label('跳转到',exact=True).locator('option:checked')).to_have_text('第4题：'+text)
            expect(number.get_by_label('跳转到',exact=True).locator('option:checked')).to_have_text('第4题：'+text)
            expect(first.get_by_label('选项 B「西瓜」跳转到',exact=True)).to_have_value('q4')
        multi.get_by_label('跳转到',exact=True).select_option('END')
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('草稿已保存')
        current=client.get('/api/surveys/'+sid,headers=headers).json()
        assert current['questions'][0]['rules']==[{'op':'eq','value':'b','target':'q3'},{'op':'eq','value':'c','target':'q4'}]
        assert current['questions'][1]['rules']==[{'op':'contains_none','value':['a','b'],'target':'END'}]
        assert current['questions'][2]['rules']==[{'op':'eq','value':1,'target':'q4'}]
        assert current['questions'][3]['id']=='q4' and current['questions'][3]['title']=='你好'
        page.reload()
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        expect(page.get_by_label('选项 B「西瓜」跳转到',exact=True)).to_have_value('q4')
        expect(page.locator('.question').first.locator('select.jump-target option[value="q4"]')).to_have_text(['第4题：你好']*3)
        page.locator('.question').first.screenshot(path=str(OUTPUT/'single-option-routing.png'))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        publish(client,headers,survey)
        endpoint='/api/public/surveys/'+sid+'/next'
        for answers,target in [({'q1':'b'},'q3'),({'q1':'c'},'q4'),({'q1':'a'},'q2')]:
            result=client.post(endpoint,headers=headers,json={'answers':answers})
            assert result.status_code==200 and result.json()['next_question_id']==target
        assert client.post(endpoint,headers=headers,json={'answers':{'q1':'a','q2':['c']}}).json()['complete']
        result=client.post(endpoint,headers=headers,json={'answers':{'q1':'a','q2':['a','c']}}).json()
        assert not result['complete'] and result['next_question_id']=='q3'
        assert errors==[]
        browser.close()


@pytest.mark.case(title='稳定选项 标题修改与网络丢包重试',input='重排选项；删除被引用项；修改已发布标题；返回修改；服务器保存后丢失响应再重试',expected='选项ID和规则不变；引用删除阻止；标题可改；当前答案保留；重试仅1份答卷')
def test_browser_audit_fixes(browser_url,client,request):
    from .conftest import login
    from .test_system import create,q
    headers=login(client,'editorowner')
    survey=create(client,headers,[q('q1',options=[{'id':x,'label':x.upper()} for x in 'abc'],rules=[{'op':'eq','value':'b','target':'END'}]),q('q2','text')])
    sid=survey['_id']
    with sync_playwright() as p:
        executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser=p.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)
        page=browser.new_page()
        page.goto(browser_url)
        page.get_by_label('用户名',exact=True).fill('editorowner')
        page.get_by_label('密码',exact=True).fill('test-password-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        page.get_by_label('选项 C',exact=True).locator('..').locator('..').get_by_role('button',name='上移',exact=True).click()
        page.get_by_label('选项 B',exact=True).locator('..').locator('..').get_by_role('button',name='上移',exact=True).click()
        page.get_by_label('选项 C',exact=True).locator('..').locator('..').get_by_role('button',name='删除选项',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('被跳转规则引用')
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('草稿已保存')
        config=client.get('/api/surveys/'+sid,headers=headers).json()
        assert [o['id'] for o in config['questions'][0]['options']]==['c','a','b']
        assert config['questions'][0]['rules'][0]['value']=='b'
        page.get_by_role('button',name='返回列表',exact=True).click()
        page.once('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='发布',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('发布成功')
        page.get_by_role('button',name='查看设置',exact=True).click()
        page.get_by_label('问卷标题',exact=True).fill('发布后修正标题')
        page.get_by_role('button',name='保存标题和说明',exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('标题和说明已更新')
        page.goto(browser_url+'/survey/'+sid)
        page.get_by_label('A. C',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        page.get_by_label('填写文本',exact=True).fill('old branch answer')
        page.get_by_role('button',name='上一题',exact=True).click()
        expect(page.get_by_label('A. C',exact=True)).to_be_checked()
        page.get_by_label('C. B',exact=True).check()
        page.get_by_role('button',name='下一步',exact=True).click()
        expect(page.get_by_role('heading',name='确认提交',exact=True)).to_be_visible()
        expect(page.get_by_text('old branch answer',exact=True)).to_have_count(0)
        attempts=[]
        def lose_response(route):
            attempts.append(route.request.post_data_json['submission_id'])
            response=route.fetch()
            assert response.status==201
            route.abort('connectionreset')
        route_pattern='**/api/public/surveys/*/responses'
        page.route(route_pattern,lose_response)
        page.get_by_role('button',name='提交答卷',exact=True).click()
        expect(page.get_by_role('alert')).not_to_be_empty()
        assert client.app.state.db.responses.count_documents({})==1
        saved_draft=page.evaluate("Object.entries(localStorage).find(([k])=>k.startsWith('survey_draft:'))[1]")
        import json
        assert json.loads(saved_draft)['submission_id']==attempts[0]
        page.unroute(route_pattern,lose_response)
        page.reload()
        expect(page.get_by_role('heading',name='确认提交',exact=True)).to_be_visible()
        page.get_by_role('button',name='提交答卷',exact=True).click()
        expect(page.get_by_role('heading',name='提交成功',exact=True)).to_be_visible()
        assert client.app.state.db.responses.count_documents({})==1
        assert page.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('survey_draft:')).length")==0
        expect(page.get_by_role('button',name='再次填写',exact=True)).to_have_count(0)
        browser.close()
