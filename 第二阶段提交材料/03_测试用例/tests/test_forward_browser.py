import os

import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import login
from .test_browser import browser_url
from .test_stage2 import saved
from .test_confirmed_model import pair
from .test_system import create, publish, q, submit


def browser_for(playwright):
    executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
    return playwright.chromium.launch(**({'executable_path':executable} if executable else {'channel':'chrome'}),headless=True)


def sign_in(page,username):
    page.get_by_label('用户名',exact=True).fill(username)
    page.get_by_label('密码',exact=True).fill('test-password-123')
    page.get_by_role('button',name='登录',exact=True).click()


@pytest.mark.case(title='多选转发页面与禁止转授权',input='⋯进入多选→勾选两题→错误用户名→正确用户名→接收者登录',expected='错误就地显示不授权；成功显示2题；共享接收者的转发禁用')
def test_browser_multi_forward(browser_url,client):
    author=login(client,'forwardui');recipient=login(client,'receiveui');single=login(client,'singlereceiveui')
    saved(client,author,title='年龄');saved(client,author,'text',title='说明')
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();errors=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        page.goto(browser_url);sign_in(page,'forwardui')
        page.get_by_role('button',name='题目与题库',exact=True).click()
        page.get_by_label('题目操作：年龄',exact=True).click()
        page.get_by_role('menuitem',name='转发',exact=True).first.click()
        dialog=page.get_by_role('dialog')
        dialog.get_by_label('转发给用户名',exact=True).fill('singlereceiveui')
        dialog.get_by_role('button',name='确认转发',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('转发 1 个题目版本')
        assert client.get('/api/questions',headers=single).json()['total']==1
        page.get_by_label('常用题目操作',exact=True).click()
        page.get_by_role('menuitem',name='多选',exact=True).first.click()
        page.get_by_role('checkbox',name='选择题目：年龄 v1',exact=True).check()
        page.get_by_role('checkbox',name='选择题目：说明 v1',exact=True).check()
        expect(page.get_by_text('已选择 2 题',exact=True)).to_be_visible()
        page.get_by_role('button',name='转发选中题目',exact=True).click()
        dialog=page.get_by_role('dialog')
        dialog.get_by_label('转发给用户名',exact=True).fill('missingname')
        dialog.get_by_role('button',name='确认转发',exact=True).click()
        expect(dialog.get_by_role('alert')).to_contain_text('目标账号不存在')
        assert client.get('/api/questions',headers=recipient).json()['total']==0
        dialog.get_by_label('转发给用户名',exact=True).fill('receiveui')
        dialog.get_by_role('button',name='确认转发',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('转发 2 个题目版本')
        page.get_by_role('button',name='退出登录',exact=True).click();sign_in(page,'receiveui')
        page.get_by_role('button',name='题目与题库',exact=True).click()
        page.get_by_label('题目操作：年龄',exact=True).click()
        expect(page.get_by_role('menuitem',name='转发',exact=True).first).to_be_disabled()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
        browser.close()




@pytest.mark.case(title='问卷版本历史页面',input='发布v1并作答→页面新建v2草稿→改题保存发布',expected='显示v2，v1题目和答卷不变，新旧链接独立')
def test_browser_survey_versions(browser_url,client):
    author=login(client,'surveyversionsui')
    a=create(client,author,[q('age','number',title='旧年龄')]);publish(client,author,a)
    assert submit(client,author,a,{'age':10}).status_code==201
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page()
        page.goto(browser_url);sign_in(page,'surveyversionsui')
        page.get_by_role('button',name='问卷版本',exact=True).click()
        page.get_by_role('dialog').get_by_role('button',name='基于 v1 新建草稿',exact=True).click()
        expect(page.get_by_text('问卷版本 v2',exact=False)).to_be_visible()
        page.get_by_label('题目标题',exact=True).fill('新年龄')
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('草稿已保存')
        page.once('dialog',lambda dialog:dialog.accept())
        page.get_by_role('button',name='直接发布',exact=True).click()
        expect(page.get_by_role('heading',name='我的问卷',exact=True)).to_be_visible()
        items=client.get('/api/surveys',headers=author).json()['items']
        assert {s['survey_version'] for s in items}=={1,2}
        assert client.get('/api/surveys/'+a['_id'],headers=author).json()['questions'][0]['title']=='旧年龄'
        assert client.get('/api/surveys/'+a['_id']+'/statistics/age',headers=author).json()['average']==10
        browser.close()
