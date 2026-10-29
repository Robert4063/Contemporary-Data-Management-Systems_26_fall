"""Pure configuration interpreter; no database, HTTP, or survey-specific conditions."""
import math


def validate_answer(question, value):
    missing = value is None or value == '' or value == [] or (isinstance(value, str) and not value.strip())
    if missing:
        return ('此题为必答题' if question['required'] else None), None
    kind, rules = question['type'], question['validation']
    options = {o['id'] for o in question['options']}
    if kind == 'single':
        if not isinstance(value, str) or value not in options:
            return '请选择一个有效选项', None
    elif kind == 'multiple':
        if not isinstance(value, list) or any(not isinstance(x, str) or x not in options for x in value):
            return '多选答案必须是有效选项 ID 数组', None
        if len(value) != len(set(value)):
            return '多选答案不能包含重复选项', None
        if not rules['min_choices'] <= len(value) <= (rules['max_choices'] or len(options)):
            return f"请选择 {rules['min_choices']} 到 {rules['max_choices'] or len(options)} 个选项", None
    elif kind == 'text':
        if not isinstance(value, str):
            return '文本答案必须是字符串', None
        if not rules['min_length'] <= len(value) <= rules['max_length']:
            return f"文本长度必须在 {rules['min_length']} 到 {rules['max_length']} 个字符之间", None
    elif kind == 'number':
        if type(value) not in (int, float) or abs(value) > 9e15 or not math.isfinite(value):
            return '请输入有限数字，绝对值不能超过 9000000000000000', None
        if rules['integer_only'] and value != math.trunc(value):
            return '必须输入整数', None
        if rules['minimum'] is not None and value < rules['minimum']:
            return f"数字不能小于 {rules['minimum']}", None
        if rules['maximum'] is not None and value > rules['maximum']:
            return f"数字不能大于 {rules['maximum']}", None
    return None, value


def matches(rule, value):
    if value is None:
        return False
    op, expected = rule['op'], rule['value']
    if op == 'eq': return value == expected
    if op == 'ne': return value != expected
    if op == 'contains_any': return any(x in value for x in expected)
    if op == 'contains_all': return all(x in value for x in expected)
    if op == 'contains_none': return not any(x in value for x in expected)
    if op == 'gt': return value > expected
    if op == 'gte': return value >= expected
    if op == 'lt': return value < expected
    if op == 'lte': return value <= expected
    if op == 'between': return expected[0] <= value <= expected[1]
    return False


def walk(questions, answers, final=False):
    """Replay from the first question. Never trust a client-supplied path."""
    known = {q['id']: i for i, q in enumerate(questions)}
    errors = [{'question_id': k, 'message': '不存在的题目'} for k in answers if k not in known]
    path, cleaned, index, next_id = [], {}, 0, None
    while index < len(questions):
        q = questions[index]
        qid = q['id']
        path.append(qid)
        if not final and qid not in answers:
            next_id = qid
            break
        error, value = validate_answer(q, answers.get(qid))
        if error:
            errors.append({'question_id': qid, 'message': error})
            next_id = qid
            break
        if value is not None:
            cleaned[qid] = value
        rules = q['rules'] if q.get('jump_enabled', bool(q['rules'])) else []
        target = next((r['target'] for r in rules if matches(r, value)), None)
        if target == 'END':
            break
        index = known[target] if target else index + 1
    for qid in answers:
        if qid in known and qid not in path:
            errors.append({'question_id': qid, 'message': '当前路径未经过此题，请移除该答案'})
    return {'path': path, 'answers': cleaned, 'next_question_id': next_id,
            'complete': next_id is None and not errors, 'errors': errors}
