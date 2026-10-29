"""One source group, explicit collection, independent deletion and shared pending UI."""
import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import login
from .test_browser import browser_url
from .test_forward_browser import browser_for,sign_in
from .test_confirmed_model import pair
from .test_stage2 import saved,reused
from .test_system import publish,submit


@pytest.mark.case(title='页面 同源版本归组且新版本显式入库',input='已有v1→页面保存v2→核对尚未入库→点击放入题库→返回',expected='保存v2不自动收录；明确入库后一个题目源卡片含两个独立版本，没有命名题库入口')
def test_browser_collection_groups_versions(browser_url,client):
    author=login(client,'groupversionsui');saved(client,author,title='年龄题第一版')
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(browser_url);sign_in(page,'groupversionsui')
        page.get_by_role('button',name='题目与题库',exact=True).click()
        expect(page.locator('.question-group')).to_have_count(1)
        expect(page.get_by_role('button',name='创建题库',exact=True)).to_have_count(0)
        page.get_by_role('button',name='创建新版本',exact=True).click()
        dialog=page.get_by_role('dialog');dialog.get_by_label('常用题标题',exact=True).fill('年龄题第二版')
        dialog.get_by_role('button',name='保存新版本',exact=True).click()
        expect(page.get_by_role('heading',name='年龄题第二版 · v2',exact=True)).to_be_visible()
        assert client.get('/api/usual-questions',headers=author).json()['total']==1
        page.get_by_role('button',name='放入题库',exact=True).click()
        expect(page.get_by_text('此版本已入库',exact=True)).to_be_visible()
        page.get_by_role('button',name='返回题目与题库',exact=True).click()
        expect(page.locator('.question-group')).to_have_count(1)
        expect(page.locator('.question-group .version-entry')).to_have_count(2)
        expect(page.get_by_role('heading',name='年龄题第一版 · v1',exact=True)).to_be_visible()
        expect(page.get_by_role('heading',name='年龄题第二版 · v2',exact=True)).to_be_visible()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
        browser.close()


@pytest.mark.case(title='页面 删除单个版本或整题收录不破坏旧问卷',input='两版已入库，v1已作答→删v2→删整题→查看原卷统计',expected='单删后保留v1，整删后库空且不会以待入库重新出现，旧答卷统计仍12')
def test_browser_collection_remove_versions(browser_url,client):
    author=login(client,'deleteentryui');v1=saved(client,author,title='第一版本')
    v2=client.post('/api/questions/'+v1['_id']+'/versions',headers=author,json={**v1['content'],'title':'第二版本'}).json()
    client.post('/api/usual-questions',headers=author,json=pair(v2))
    survey=reused(client,author,v1);publish(client,author,survey);submit(client,author,survey,{'q1':12})
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'deleteentryui')
        page.get_by_role('button',name='题目与题库',exact=True).click()
        row=page.locator('.version-entry').filter(has=page.get_by_role('heading',name='第二版本 · v2',exact=True))
        page.once('dialog',lambda d:d.accept());row.get_by_role('button',name='删除此版本',exact=True).click()
        expect(page.locator('.question-group .version-entry')).to_have_count(1)
        expect(page.get_by_role('heading',name='第一版本 · v1',exact=True)).to_be_visible()
        page.once('dialog',lambda d:d.accept())
        page.get_by_role('button',name='删除此题的所有收录版本',exact=True).click()
        expect(page.locator('.question-group')).to_have_count(0)
        expect(page.locator('.pending-group')).to_have_count(0)
        expect(page.get_by_text('暂无已入库题目。',exact=True)).to_be_visible()
        assert client.get('/api/surveys/'+survey['_id']+'/statistics/q1',headers=author).json()['average']==12
        browser.close()


@pytest.mark.case(title='页面 收到共享后主动入库，删除只影响自己',input='作者共享v1→接收者登录看待入库→明确放入题库→删除自己的收录',expected='共享起初不在题库，放入后有一组；不能转授权，删除不影响作者题库')
def test_browser_shared_collection_is_explicit(browser_url,client):
    author=login(client,'collectauthorui');receiver=login(client,'collectstudentui');version=saved(client,author,title='共享年龄题')
    client.post('/api/questions/'+version['_id']+'/shares',headers=author,json={'username':'collectstudentui'})
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'collectstudentui')
        page.get_by_role('button',name='题目与题库',exact=True).click()
        expect(page.locator('.question-group')).to_have_count(0)
        expect(page.locator('.pending-group')).to_have_count(1)
        page.get_by_role('button',name='放入题库',exact=True).click()
        expect(page.locator('.question-group')).to_have_count(1)
        expect(page.locator('.pending-group')).to_have_count(0)
        page.get_by_label('题目操作：共享年龄题',exact=True).click()
        expect(page.get_by_role('menuitem',name='转发',exact=True)).to_be_disabled()
        page.get_by_label('题目操作：共享年龄题',exact=True).click()
        page.once('dialog',lambda d:d.accept())
        page.get_by_role('button',name='删除此题的所有收录版本',exact=True).click()
        expect(page.locator('.question-group')).to_have_count(0)
        assert client.get('/api/usual-questions',headers=author).json()['total']==1
        assert client.get('/api/usual-questions',headers=receiver).json()['total']==0
        browser.close()
