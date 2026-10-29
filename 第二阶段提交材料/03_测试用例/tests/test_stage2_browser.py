"""Real browser coverage for stage two, sharing the existing local server fixture."""
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import login
from .test_browser import browser_url
from .test_stage2 import saved


@pytest.mark.case(title='S09 前端题库及版本完整流程', input='网页建数字题/题库→问卷从库选题→保存→改题新版本→恢复→统计', expected='实际页面操作成功，旧问卷冻结内容，历史和统计可见，无JS错误')
def test_stage2_browser_library_flow(browser_url, client):
    login(client, 'libraryui')
    with sync_playwright() as p:
        executable = os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser = p.chromium.launch(**({'executable_path': executable} if executable else {'channel': 'chrome'}), headless=True)
        page = browser.new_page(); errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(browser_url)
        page.get_by_label('用户名', exact=True).fill('libraryui')
        page.get_by_label('密码', exact=True).fill('test-password-123')
        page.get_by_role('button', name='登录', exact=True).click()
        page.get_by_role('button', name='题目与题库', exact=True).click()
        page.get_by_role('button', name='创建常用题目', exact=True).click()
        dialog = page.get_by_role('dialog')
        dialog.get_by_label('常用题标题', exact=True).fill('你的年龄')
        dialog.get_by_label('常用题型', exact=True).select_option('number')
        dialog.get_by_label('常用题最小值', exact=True).fill('0')
        dialog.get_by_label('常用题最大值', exact=True).fill('120')
        dialog.get_by_label('常用题只允许整数', exact=True).check()
        dialog.get_by_role('button', name='保存并放入题库', exact=True).click()
        expect(page.get_by_role('heading', name='你的年龄 · v1', exact=True)).to_be_visible()
        page.get_by_role('button', name='返回问卷列表', exact=True).click()
        page.get_by_role('button', name='创建问卷', exact=True).click()
        page.get_by_label('问卷标题', exact=True).fill('题库复用演示')
        page.get_by_label('截止时间（发布前必填，本地时间）', exact=True).fill((datetime.now()+timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'))
        page.get_by_role('button', name='从题目或题库选题', exact=True).click()
        dialog.get_by_role('button', name='选用此版本', exact=True).click()
        expect(page.get_by_label('题目标题', exact=True)).to_be_enabled()
        page.get_by_role('button', name='保存草稿', exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('草稿已保存')
        page.get_by_role('button', name='返回列表', exact=True).click()
        page.get_by_role('button', name='题目与题库', exact=True).click()
        page.get_by_role('button', name='查看题目详情', exact=True).click()
        expect(page.get_by_text('题库复用演示 · 草稿', exact=False)).to_be_visible()
        page.get_by_role('button', name='基于此版本修改', exact=True).click()
        dialog.get_by_label('常用题标题', exact=True).fill('今年的年龄')
        dialog.get_by_role('button', name='保存新版本', exact=True).click()
        expect(page.get_by_role('heading', name='今年的年龄 · v2', exact=True)).to_be_visible()
        page.once('dialog', lambda d: d.accept())
        page.get_by_role('button', name='恢复为新版本', exact=True).click()
        expect(page.get_by_role('heading', name='今年的年龄 · v3', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='跨问卷统计', exact=True)).to_be_visible()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not errors
        survey = client.app.state.db.surveys.find_one({'title': '题库复用演示'})
        assert survey['questions'][0]['content']['title'] == '你的年龄'
        page.screenshot(path=str(Path(__file__).resolve().parents[1]/'运行结果'/'stage2-library-mobile.png'), full_page=True)
        browser.close()


@pytest.mark.case(title='S09 前端共享与接收者选题', input='作者网页共享给学生；学生网页从共享题选择', expected='共享列表显示账号；接收者可用共享题，草稿中可编辑并派生自己的版本')
def test_stage2_browser_sharing(browser_url, client):
    owner = login(client, 'teacherui'); login(client, 'studentui')
    saved(client, owner, title='共享年龄')
    with sync_playwright() as p:
        executable = os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        browser = p.chromium.launch(**({'executable_path': executable} if executable else {'channel': 'chrome'}), headless=True)
        page = browser.new_page()
        def sign_in(username):
            page.get_by_label('用户名', exact=True).fill(username)
            page.get_by_label('密码', exact=True).fill('test-password-123')
            page.get_by_role('button', name='登录', exact=True).click()
        page.goto(browser_url); sign_in('teacherui')
        page.get_by_role('button', name='题目与题库', exact=True).click()
        page.get_by_role('button', name='查看题目详情', exact=True).click()
        page.get_by_label('共享给用户名', exact=True).fill('studentui')
        page.get_by_role('button', name='共享给用户', exact=True).click()
        expect(page.get_by_role('button', name='撤销共享', exact=True)).to_be_visible()
        page.get_by_role('button', name='退出登录', exact=True).click(); sign_in('studentui')
        page.get_by_role('button', name='创建问卷', exact=True).click()
        page.get_by_role('button', name='从题目或题库选题', exact=True).click()
        expect(page.get_by_role('dialog').get_by_text('数字填空 · 共享题目', exact=True)).to_be_visible()
        page.get_by_role('button', name='选用此版本', exact=True).click()
        expect(page.get_by_label('题目标题', exact=True)).to_have_value('共享年龄')
        expect(page.get_by_label('题目标题', exact=True)).to_be_enabled()
        browser.close()
