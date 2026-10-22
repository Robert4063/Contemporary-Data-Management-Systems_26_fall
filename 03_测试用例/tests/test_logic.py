import pytest
from app.logic import matches, walk
from app.models import Question
from .test_system import q


@pytest.mark.parametrize('op,value,expected,match',[
    ('eq',2,2,True),('ne',2,3,True),('gt',3,2,True),('gt',2,2,False),
    ('gte',2,2,True),('lt',1,2,True),('lte',2,2,True),
    ('between',2,[2,3],True),('between',3,[2,3],True),('between',4,[2,3],False),
    ('contains_any',['a'],['a','b'],True),('contains_any',['a'],['b'],False),
    ('contains_all',['a','b'],['a','b'],True),('contains_all',['a'],['a','b'],False),
    ('contains_none',['c'],['a','b'],True),('contains_none',['a','c'],['a','b'],False),
    ('contains_none',['a','b'],['a','b'],False),('contains_none',None,['a','b'],False),
    ('eq',None,None,False),
])
@pytest.mark.case(title='全部跳转操作符',input='各操作符及边界参数',expected='matches返回参数中指定的布尔值，空答案不触发规则')
def test_operators(op,value,expected,match):
    assert matches({'op':op,'value':expected},value) is match


@pytest.mark.case(title='旧规则解释与未命中顺序执行',input='历史数字多规则数据；未命中；可选题留空',expected='旧数据首条命中q3；未命中q2；新配置不允许多规则，见API回归')
def test_order_and_optional():
    questions=[q('q1','number'),q('q2','text'),q('q3','text')]
    questions=[Question.model_validate(x).model_dump() for x in questions]
    questions[0].pop('jump_enabled')  # Historical document without an explicit flag.
    questions[0]['rules']=[{'op':'gt','value':0,'target':'q3'},{'op':'gte','value':1,'target':'END'}]
    assert walk(questions,{'q1':1})['next_question_id']=='q3'
    assert walk(questions,{'q1':0})['next_question_id']=='q2'
    questions[0]['required']=False
    questions[0]['rules']=[{'op':'ne','value':0,'target':'END'}]
    assert walk(questions,{'q1':None})['next_question_id']=='q2'
