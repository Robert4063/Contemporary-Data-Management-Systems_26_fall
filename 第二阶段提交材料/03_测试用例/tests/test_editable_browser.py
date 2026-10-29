"""Create from the current question and edit both resulting drafts in Chrome."""
import pytest
from playwright.sync_api import expect, sync_playwright

from .conftest import login
from .test_browser import browser_url
from .test_forward_browser import browser_for, sign_in
from .test_module_flow import source_question
from .test_stage2 import saved
from .test_system import create, q


@pytest.mark.case(title='页面 从当前题生成下一问卷版本且两版均能编辑',input='原卷两题，修改来源题但未保存→题旁新建→改新版本保存→返回改旧版本',expected='新版本保留全部题目；两版可编辑且互不覆盖，题库原版不变')
def test_browser_both_drafts_editable(browser_url,client):
    author=login(client,'bothdraftsui');version=saved(client,author,title='题库里的原题')
    original=create(client,author,[source_question(version,'selected'),q('other','text',title='另一道原卷题')])
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
        page.goto(browser_url);sign_in(page,'bothdraftsui')
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        first=page.locator('.question').first
        expect(first.get_by_label('题目标题',exact=True)).to_be_enabled()
        first.get_by_label('题目标题',exact=True).fill('从当前编辑题生成')
        first.get_by_role('button',name='基于此题创建下一版',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('两份草稿均可继续编辑')
        expect(page.locator('.question')).to_have_count(2)
        expect(page.get_by_label('题目标题',exact=True).first).to_be_enabled()
        expect(page.get_by_label('题目标题',exact=True).first).to_have_value('从当前编辑题生成')
        expect(page.get_by_label('题目标题',exact=True).last).to_have_value('另一道原卷题')
        page.get_by_label('问卷标题',exact=True).fill('新的独立问卷')
        page.get_by_label('题目标题',exact=True).first.fill('新卷单独修改')
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('草稿已保存')
        page.get_by_role('button',name='返回列表',exact=True).click()
        old_card=page.locator('.card').filter(has=page.get_by_role('heading',name=original['title'],exact=True))
        old_card.get_by_role('button',name='编辑问卷',exact=True).click()
        expect(page.locator('.question')).to_have_count(2)
        expect(page.get_by_label('题目标题',exact=True).first).to_be_enabled()
        expect(page.get_by_label('题目标题',exact=True).first).to_have_value('从当前编辑题生成')
        page.get_by_label('题目标题',exact=True).first.fill('旧卷单独修改')
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('草稿已保存')
        data=client.get('/api/surveys',headers=author).json()['items']
        new=next(s for s in data if s['_id']!=original['_id'])
        assert new['survey_id']==original['survey_id'] and new['survey_version']==2 and new['parent_version']==1
        assert client.get('/api/surveys/'+new['_id'],headers=author).json()['questions'][0]['title']=='新卷单独修改'
        assert client.get('/api/surveys/'+original['_id'],headers=author).json()['questions'][0]['title']=='旧卷单独修改'
        assert client.get('/api/questions/'+version['_id'],headers=author).json()['content']['title']=='题库里的原题'
        assert not errors
        browser.close()


@pytest.mark.case(title='页面 未保存普通题生成下一版本时保留全部题',input='草稿中添加两题，未手动保存，点击第二题旁新建',expected='原卷及下一版本都保存两题；内容顺序不丢失，不要求先入题库')
def test_browser_unsaved_question_to_survey(browser_url,client):
    author=login(client,'unsavedquestionui')
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'unsavedquestionui')
        page.get_by_role('button',name='创建问卷',exact=True).click()
        page.get_by_label('问卷标题',exact=True).fill('原来的双题问卷')
        for title in ['第一题','只基于第二题新建']:
            page.get_by_role('button',name='添加题目',exact=True).click()
            page.locator('.question').last.get_by_label('题目标题',exact=True).fill(title)
        page.locator('.question').last.get_by_role('button',name='基于此题创建下一版',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('两份草稿均可继续编辑')
        expect(page.locator('.question')).to_have_count(2)
        expect(page.get_by_label('题目标题',exact=True).first).to_have_value('第一题')
        expect(page.get_by_label('题目标题',exact=True).last).to_have_value('只基于第二题新建')
        page.get_by_label('题目标题',exact=True).last.fill('新卷第二题改写')
        page.get_by_role('button',name='保存草稿',exact=True).click()
        expect(page.get_by_role('alert').first).to_contain_text('草稿已保存')
        all_surveys=client.get('/api/surveys',headers=author).json()['items']
        assert len(all_surveys)==2
        original=next(s for s in all_surveys if s['survey_version']==1)
        assert [item['title'] for item in client.get('/api/surveys/'+original['_id'],headers=author).json()['questions']]==['第一题','只基于第二题新建']
        browser.close()


@pytest.mark.case(title='页面 草稿连续生成下一版本并保留全卷及跳转',input='三题问卷从中间题生成v2，再从末题生成v3，打开版本历史',expected='三版同属一卷，全部三题和原跳转保留，三份草稿都可继续编辑')
def test_browser_next_versions_keep_all_questions(browser_url,client):
    author=login(client,'allquestionsnextui')
    original=create(client,author,[q('first',rules=[{'op':'eq','value':'a','target':'last'}],jump_enabled=True),
        q('middle','number'),q('last','text')],title='保留整卷的版本链')
    with sync_playwright() as p:
        browser=browser_for(p);page=browser.new_page();page.goto(browser_url);sign_in(page,'allquestionsnextui')
        page.get_by_role('button',name='编辑问卷',exact=True).click()
        page.locator('.question').nth(1).get_by_role('button',name='基于此题创建下一版',exact=True).click()
        expect(page.get_by_text('问卷版本 v2',exact=False)).to_be_visible()
        expect(page.locator('.question')).to_have_count(3)
        page.locator('.question').last.get_by_role('button',name='基于此题创建下一版',exact=True).click()
        expect(page.get_by_text('问卷版本 v3',exact=False)).to_be_visible()
        expect(page.locator('.question')).to_have_count(3)
        rows=client.get('/api/surveys',headers=author).json()['items']
        assert {row['survey_version'] for row in rows}=={1,2,3}
        for row in rows:
            survey=client.get('/api/surveys/'+row['_id'],headers=author).json()
            assert survey['survey_id']==original['survey_id'] and survey['status']=='draft'
            assert [item['id'] for item in survey['questions']]==['first','middle','last']
            assert survey['questions'][0]['rules']==original['questions'][0]['rules']
        page.get_by_role('button',name='问卷版本',exact=True).click()
        dialog=page.get_by_role('dialog')
        for number in (1,2,3):
            expect(dialog.get_by_role('heading',name=f'问卷 v{number} · 保留整卷的版本链',exact=True)).to_be_visible()
            expect(dialog.get_by_role('button',name=f'基于 v{number} 新建草稿',exact=True)).to_be_visible()
        browser.close()
