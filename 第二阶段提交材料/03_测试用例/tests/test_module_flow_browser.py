"""Real browser coverage for saving before navigation and complete version previews."""
from copy import deepcopy

from bson import ObjectId
import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import login
from .test_forward_browser import browser_for, sign_in
from .test_confirmed_model import pair
from .test_module_flow import next_question, source_question
from .test_stage2 import saved
from .test_system import create, publish, submit
from .test_browser import browser_url


@pytest.mark.case(title='页面 保存原草稿后用题新建，失败不跳走',input='未设截止，编辑原卷标题与题目；模拟新建失败，再重试成功',expected='失败留在当前对话框和原编辑；原草稿已保存；成功打开独立新卷')
def test_browser_save_before_new_survey(browser_url,client):
    author=login(client,'draftflowui');version=saved(client,author,title='新卷选用的题')
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'draftflowui')
        page.get_by_role('button',name='创建问卷',exact=True).click()
        page.get_by_label('问卷标题',exact=True).fill('必须保留的编辑')
        page.get_by_role('button',name='添加题目',exact=True).click()
        page.get_by_label('题目标题',exact=True).fill('原问卷的问题')
        old=client.get('/api/surveys',headers=author).json()['items'][0]
        page.get_by_role('button',name='从题目或题库选题',exact=True).click()
        page.route('**/api/surveys/*/versions',lambda route:route.fulfill(status=503,json={'detail':'模拟创建失败'}))
        dialog=page.get_by_role('dialog');dialog.get_by_role('button',name='选用此题创建下一版',exact=True).click()
        expect(dialog.get_by_role('alert')).to_contain_text('模拟创建失败')
        assert client.app.state.db.surveys.count_documents({})==1
        retained=client.get('/api/surveys/'+old['_id'],headers=author).json()
        assert retained['title']=='必须保留的编辑' and retained['deadline'] is None
        assert retained['questions'][0]['title']=='原问卷的问题'
        page.unroute('**/api/surveys/*/versions')
        dialog.get_by_role('button',name='选用此题创建下一版',exact=True).click()
        expect(dialog).to_have_count(0)
        expect(page.get_by_role('alert').first).to_contain_text('已打开问卷 v2')
        expect(page.get_by_label('问卷标题',exact=True)).to_have_value('必须保留的编辑')
        expect(page.get_by_label('题目标题',exact=True).first).to_have_value('原问卷的问题')
        expect(page.get_by_label('题目标题',exact=True).last).to_have_value('新卷选用的题')
        surveys=client.get('/api/surveys',headers=author).json()['items']
        assert len(surveys)==2 and all(s['status']=='draft' for s in surveys)
        new=next(s for s in surveys if s['_id']!=old['_id'])
        assert new['survey_id']==old['survey_id'] and new['survey_version']==2
        assert client.get('/api/surveys/'+new['_id'],headers=author).json()['questions'][-1]['question_id']==version['question_id']
        browser.close()


@pytest.mark.case(title='页面 逐版预览后切换题目',input='单选v1、数字v2；编辑问卷说明；逐版查看选项/范围并选择v2',expected='所有版本内容可见；只替换本题，保留本地ID及编辑说明，重开仍v2')
def test_browser_question_version_preview(browser_url,client):
    author=login(client,'versionpickui')
    first=saved(client,author,'single',title='原来的选择')
    second=next_question(client,author,first,title='新的年龄',type='number',options=[],validation={'minimum':18,'maximum':80,'integer_only':True})
    s=client.post('/api/surveys/from-questions',headers=author,json={'items':[pair(first)]}).json()
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(browser_url);sign_in(page,'versionpickui')
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        page.get_by_label('问卷说明',exact=True).fill('切换时也要保存的说明')
        page.get_by_role('button',name='切换题目版本',exact=True).click()
        dialog=page.get_by_role('dialog')
        expect(dialog.get_by_role('heading',name='原来的选择 · v1',exact=True)).to_be_visible()
        expect(dialog.get_by_text('A. A',exact=True)).to_be_visible()
        expect(dialog.get_by_text('B. B',exact=True)).to_be_visible()
        expect(dialog.get_by_role('heading',name='新的年龄 · v2',exact=True)).to_be_visible()
        expect(dialog.get_by_text('数字范围：18 至 80；只允许整数',exact=True)).to_be_visible()
        dialog.get_by_role('button',name='使用 v2',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('版本已切换')
        expect(page.get_by_label('题目标题',exact=True)).to_have_value('新的年龄')
        next_question(client,author,second,title='刚创建的第三版')
        page.get_by_role('button',name='切换题目版本',exact=True).click()
        expect(page.get_by_role('dialog').get_by_role('heading',name='刚创建的第三版 · v3',exact=True)).to_be_visible()
        page.get_by_role('dialog').get_by_role('button',name='取消',exact=True).click()
        current=client.get('/api/surveys/'+s['_id'],headers=author).json()
        assert current['description']=='切换时也要保存的说明'
        assert current['questions'][0]['id']=='q1' and current['questions'][0]['question_version']==2
        page.get_by_role('button',name='返回列表',exact=True).click()
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        expect(page.get_by_label('题目标题',exact=True)).to_have_value('新的年龄')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
        browser.close()


@pytest.mark.case(title='页面 未共享版本没有切换按钮',input='作者创建v2但只共享v1；学生编辑使用v1的问卷',expected='学生仍能使用原题，不显示未获授权的版本与切换入口')
def test_browser_switch_respects_permissions(browser_url,client):
    author=login(client,'pickauthorui');receiver=login(client,'pickreceiverui')
    first=saved(client,author);next_question(client,author,first,title='不应看见的私有版本')
    client.post('/api/questions/'+first['_id']+'/shares',headers=author,json={'username':'pickreceiverui'})
    client.post('/api/surveys/from-questions',headers=receiver,json={'items':[pair(first)]})
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'pickreceiverui')
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        expect(page.get_by_role('heading',name='问卷设置',exact=True)).to_be_visible()
        expect(page.get_by_role('button',name='切换题目版本',exact=True)).to_have_count(0)
        expect(page.get_by_text('不应看见的私有版本',exact=True)).to_have_count(0)
        browser.close()


@pytest.mark.case(title='页面 换版明确清除失效跳转',input='单选题已有规则，选择文本新版，默认失败，再勾选清除规则重试',expected='默认不改题；确认清除后成功，规则为空')
def test_browser_switch_incompatible_rules(browser_url,client):
    author=login(client,'switchrulesui');first=saved(client,author,'single')
    next_question(client,author,first,title='文字说明新版',type='text',options=[])
    s=create(client,author,[source_question(first,rules=[{'op':'eq','value':'a','target':'END'}],jump_enabled=True)])
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'switchrulesui')
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        page.get_by_role('button',name='切换题目版本',exact=True).click()
        dialog=page.get_by_role('dialog');dialog.get_by_role('button',name='使用 v2',exact=True).click()
        expect(dialog.get_by_role('alert')).to_contain_text('跳转规则不兼容')
        assert client.get('/api/surveys/'+s['_id'],headers=author).json()['questions'][0]['question_version']==1
        dialog.get_by_label('清除本题跳转规则',exact=True).check()
        dialog.get_by_role('button',name='使用 v2',exact=True).click()
        expect(page.get_by_label('题目标题',exact=True)).to_have_value('文字说明新版')
        assert client.get('/api/surveys/'+s['_id'],headers=author).json()['questions'][0]['rules']==[]
        browser.close()


@pytest.mark.case(title='页面 发布题目换版自动进入新草稿',input='已发布v1有答卷；题旁选择v2并生成下一问卷版本',expected='打开问卷v2草稿；旧发布文档和答卷完全保留')
def test_browser_published_question_switch(browser_url,client):
    author=login(client,'publishedpickui');first=saved(client,author);next_question(client,author,first,title='发布后的新版题')
    s=create(client,author,[source_question(first)]);publish(client,author,s);submit(client,author,s,{'shared':10})
    db=client.app.state.db;old=deepcopy(db.surveys.find_one({'_id':ObjectId(s['_id'])}));answer=deepcopy(db.responses.find_one())
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'publishedpickui')
        page.get_by_role('button',name='查看设置',exact=True).click()
        page.get_by_role('button',name='切换题目版本',exact=True).click()
        page.get_by_role('dialog').get_by_role('button',name='使用 v2 新建草稿',exact=True).click()
        expect(page.get_by_text('问卷版本 v2',exact=False)).to_be_visible()
        expect(page.get_by_label('题目标题',exact=True)).to_have_value('发布后的新版题')
        assert db.surveys.find_one({'_id':old['_id']})==old and db.responses.find_one()==answer
        browser.close()
