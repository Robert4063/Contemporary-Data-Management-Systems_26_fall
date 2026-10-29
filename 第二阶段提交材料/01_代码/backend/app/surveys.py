"""Survey ownership, draft composition, immutable publications and versions."""
from copy import deepcopy
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pydantic import ValidationError

from .versions import store_questions, survey_view
from .models import DeadlineInput, MetadataUpdate, Question, SurveyInput, SurveyUpdate
from .security import current_user, now

from .common import oid, public_document, check_deadline
from .permissions import owned_document
from .models import ReuseQuestion
from .models import DraftSave, SurveyFromQuestions, SwitchQuestionVersion, SurveyVersionChoice, SurveyDraftVersion
from .versions import get_ref, get_version, encode

router = APIRouter(tags=['问卷'])


def replace_version(db, survey, local_id, choice, user):
    """Resolve a permitted module reference, preserving local identity and routing."""
    target = next((q for q in survey['questions'] if q['id'] == local_id), None)
    if target is None:
        raise HTTPException(404, '题目不存在')
    if str(target.get('question_id')) != choice.question_id:
        raise HTTPException(422, '只能切换同一道题目的版本')
    version = get_ref(db, choice.model_dump(), user)
    question = {**target, **version['content'], 'question_id': str(version['question_id']),
        'question_version': version['question_version'], 'source_version_id': str(version['_id'])}
    if choice.clear_rules:
        question.update(rules=[], jump_enabled=False)
    if version['content']['type'] == 'text':
        question['jump_enabled'] = False
    items = [question if q['id'] == local_id else q for q in survey['questions']]
    try:
        DraftSave.model_validate({**{k: survey[k] for k in SurveyInput.model_fields},
            'questions': items, 'revision': survey['revision']})
    except ValidationError:
        raise HTTPException(422, '此版本与当前跳转规则不兼容，请勾选“清除本题跳转规则”后重试')
    return store_questions(db, items, user, survey['questions'])


@router.post('/api/surveys/from-questions', status_code=201)
def from_questions(body: SurveyFromQuestions, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    # Validate the complete selection before creating any new question or survey.
    versions = [get_ref(db, item.model_dump(), user) for item in body.items]
    questions = [Question(id='q'+str(i+1), question_id=str(v['question_id']),
        question_version=v['question_version'], source_version_id=str(v['_id']), **v['content']).model_dump()
        for i, v in enumerate(versions)]
    return insert_draft({'title': '未命名问卷', 'description': '', 'allow_anonymous': False,
        'allow_multiple': False, 'deadline': None, 'questions': questions}, request, user, incomplete=True)


@router.patch('/api/surveys/{survey_id}/draft-content')
def save_draft(survey_id: str, body: DraftSave, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    if survey['status'] != 'draft' or survey['revision'] != body.revision:
        raise HTTPException(409, '仅草稿可以保存，或编辑修订已更新，请重新加载')
    values = body.model_dump(exclude={'revision'})
    values['questions'] = store_questions(request.app.state.db, values['questions'], user, survey['questions'], allow_existing_edits=True)
    result = request.app.state.db.surveys.find_one_and_update(
        {'_id': survey['_id'], 'owner_id': user['_id'], 'status': 'draft', 'revision': body.revision},
        {'$set': {**values, 'updated_at': now()}, '$inc': {'revision': 1}}, return_document=ReturnDocument.AFTER)
    if result is None:
        raise HTTPException(409, '草稿已更新，请重新加载')
    return public_document(survey_view(request.app.state.db, result))


@router.put('/api/surveys/{survey_id}/questions/{local_id}/version')
def switch_version(survey_id: str, local_id: str, body: SwitchQuestionVersion, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    if survey['status'] != 'draft' or survey['revision'] != body.revision:
        raise HTTPException(409, '只能切换草稿中的题目版本，或编辑修订已更新')
    db = request.app.state.db
    questions = replace_version(db, survey, local_id, body, user)
    result = db.surveys.find_one_and_update(
        {'_id': survey['_id'], 'owner_id': user['_id'], 'status': 'draft', 'revision': body.revision},
        {'$set': {'questions': questions, 'updated_at': now()}, '$inc': {'revision': 1}}, return_document=ReturnDocument.AFTER)
    if result is None:
        raise HTTPException(409, '草稿已更新，请重新加载')
    return public_document(survey_view(db, result))


def owned(request, survey_id, user):
    survey = owned_document(request.app.state.db.surveys, survey_id, user, '问卷')
    return survey_view(request.app.state.db, survey)

def shared_survey(request, survey_id):
    survey = request.app.state.db.surveys.find_one({'_id': oid(survey_id)})
    if not survey or survey['status'] == 'draft':
        raise HTTPException(404, '问卷不存在或尚未发布')
    return survey_view(request.app.state.db, survey)

def survey_state(survey):
    server_time = now()
    if survey['status'] == 'closed':
        availability, message = 'closed', '问卷已关闭，无法继续填写或提交。'
    elif survey.get('deadline') is None:
        availability, message = 'incomplete', '问卷未设置截止时间，请联系创建者补充。'
    elif survey['deadline'] <= server_time:
        availability, message = 'expired', '问卷已截止，无法继续填写或提交。'
    else:
        availability, message = 'open', '问卷正在收集中，登录后可以填写。'
    return {'availability': availability, 'can_fill': availability == 'open',
            'message': message, 'server_time': server_time}

def available(request, survey_id):
    survey = shared_survey(request, survey_id)
    state = survey_state(survey)
    if not state['can_fill']:
        raise HTTPException(410, state['message'])
    return survey

def insert_draft(data, request, user, incomplete=False):
    created_at = now()
    created_at = created_at.replace(microsecond=created_at.microsecond // 1000 * 1000)
    if not incomplete:
        check_deadline(data.get('deadline'), created_at)
    data['questions'] = store_questions(request.app.state.db, data['questions'], user)
    identity = ObjectId()
    doc = {**data, '_id': identity, 'survey_id': identity, 'survey_version': 1, 'parent_version': None,
           'owner_id': user['_id'], 'status': 'draft', 'revision': 1,
           'schema_version': 2, 'created_at': created_at, 'updated_at': created_at, 'admissions': 0}
    request.app.state.db.surveys.insert_one(doc)
    return public_document(survey_view(request.app.state.db, doc))

@router.post('/api/surveys', status_code=201)
def create(body: SurveyInput, request: Request, user=Depends(current_user)):
    return insert_draft(body.model_dump(), request, user)

@router.post('/api/surveys/drafts', status_code=201)
def begin_draft(request: Request, user=Depends(current_user)):
    # The create-button request reaches the server before editing begins.
    return insert_draft({'title': '未命名问卷', 'description': '', 'allow_anonymous': False,
        'allow_multiple': False, 'deadline': None, 'questions': []}, request, user, incomplete=True)

@router.get('/api/surveys')
def listing(request: Request, skip: int = 0, limit: int = 50, user=Depends(current_user)):
    if skip < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, 'skip 必须非负，limit 必须为 1 到 100')
    db = request.app.state.db
    query = {'owner_id': user['_id']}
    return {'items': public_document(list(db.surveys.find(query, {'questions': 0}).sort('created_at', -1).skip(skip).limit(limit))),
            'total': db.surveys.count_documents(query)}

@router.get('/api/surveys/{survey_id}')
def detail(survey_id: str, request: Request, user=Depends(current_user)):
    return public_document(owned(request, survey_id, user))

@router.put('/api/surveys/{survey_id}')
def edit(survey_id: str, body: SurveyUpdate, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    check_deadline(body.deadline, survey['created_at'])
    values = body.model_dump(exclude={'revision'})
    if survey['status'] != 'draft' or survey['revision'] != body.revision:
        raise HTTPException(409, '仅草稿可以编辑，或版本已更新')
    values['questions'] = store_questions(request.app.state.db, values['questions'], user, survey['questions'], allow_existing_edits=True)
    result = request.app.state.db.surveys.find_one_and_update(
        {'_id': survey['_id'], 'status': 'draft', 'revision': body.revision},
        {'$set': {**values, 'updated_at': now()}, '$inc': {'revision': 1}}, return_document=ReturnDocument.AFTER)
    if not result:
        raise HTTPException(409, '仅草稿可以编辑，或版本已更新；请重新加载')
    return public_document(survey_view(request.app.state.db, result))

@router.get('/api/surveys/{survey_id}/versions')
def survey_versions(survey_id: str, request: Request, user=Depends(current_user)):
    source = owned(request, survey_id, user)
    fields = {'questions': 0, 'admissions': 0}
    rows = list(request.app.state.db.surveys.find(
        {'survey_id': source['survey_id'], 'owner_id': user['_id']}, fields).sort('survey_version', 1))
    return {'survey_id': str(source['survey_id']), 'items': public_document(rows)}

@router.post('/api/surveys/{survey_id}/versions', status_code=201)
def new_survey_version(survey_id: str, request: Request, body: SurveyVersionChoice | SurveyDraftVersion | None = None, user=Depends(current_user)):
    source = owned(request, survey_id, user)
    db = request.app.state.db
    raw = db.surveys.find_one({'_id': source['_id']})
    if raw['revision'] != source['revision'] or isinstance(body, SurveyDraftVersion) and body.revision != source['revision']:
        raise HTTPException(409, '原问卷已更新，请重新加载后创建下一版本')
    doc = {k: deepcopy(raw[k]) for k in SurveyInput.model_fields}
    if isinstance(body, SurveyVersionChoice):
        doc['questions'] = replace_version(db, source, body.local_id, body, user)
    elif isinstance(body, SurveyDraftVersion):
        if body.local_id and not any(q['id'] == body.local_id for q in source['questions']):
            raise HTTPException(404, '依据的题目不在原问卷中')
        if body.selected_question is not None:
            version = get_ref(db, body.selected_question.model_dump(), user)
            already_present = any(q.get('question_id') == version['question_id'] and
                q.get('question_version') == version['question_version'] for q in doc['questions'])
            if not already_present:
                used = {q['id'] for q in doc['questions']};number = 1
                while 'q'+str(number) in used:
                    number += 1
                question = Question(id='q'+str(number), question_id=str(version['question_id']),
                    question_version=version['question_version'], source_version_id=str(version['_id']), **version['content']).model_dump()
                doc['questions'].append(store_questions(db, [question], user)[0])
    try:
        DraftSave.model_validate({**{k: source[k] for k in SurveyInput.model_fields},
            'questions': survey_view(db, {'_id': source['_id'], 'questions': doc['questions']})['questions'], 'revision': source['revision']})
    except ValidationError:
        raise HTTPException(422, '问卷题目数量或跳转配置不合法，不能创建下一版本')
    stamp = now()
    doc.update({'survey_id': source['survey_id'],
        'parent_version': source['survey_version'], 'owner_id': user['_id'], 'status': 'draft',
        'revision': 1, 'schema_version': 2, 'created_at': stamp, 'updated_at': stamp, 'admissions': 0})
    if not doc.get('deadline') or doc['deadline'] <= stamp:
        doc['deadline'] = None
    for _ in range(40):
        latest = db.surveys.find_one({'survey_id': source['survey_id']}, sort=[('survey_version', -1)])
        doc['survey_version'] = latest['survey_version']+1
        doc.pop('_id', None)
        try:
            db.surveys.insert_one(doc)
            return public_document(survey_view(db, doc))
        except DuplicateKeyError:
            continue
    raise HTTPException(409, '并发创建版本较多，请稍后重试')

@router.post('/api/surveys/{survey_id}/questions', status_code=201)
def add_question(survey_id: str, body: Question, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    if survey['status'] != 'draft':
        raise HTTPException(409, '仅草稿可以添加题目')
    data = {k: survey[k] for k in SurveyInput.model_fields}
    data['questions'] = [*survey['questions'], body.model_dump()]
    try:
        SurveyInput.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
    snapshot = store_questions(request.app.state.db, [body.model_dump()], user)[0]
    result = request.app.state.db.surveys.find_one_and_update(
        {'_id': survey['_id'], 'status': 'draft', 'revision': survey['revision']},
        {'$push': {'questions': snapshot}, '$inc': {'revision': 1}, '$set': {'updated_at': now()}},
        return_document=ReturnDocument.AFTER)
    if not result:
        raise HTTPException(409, '仅草稿可以添加题目，或版本已更新')
    return public_document(survey_view(request.app.state.db, result))

@router.patch('/api/surveys/{survey_id}/metadata')
def metadata(survey_id: str, body: MetadataUpdate, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    result = request.app.state.db.surveys.find_one_and_update(
        {'_id': survey['_id'], 'revision': body.revision},
        {'$set': {'title': body.title, 'description': body.description, 'updated_at': now()},
         '$inc': {'revision': 1}}, return_document=ReturnDocument.AFTER)
    if not result:
        raise HTTPException(409, '问卷版本已更新，请重新加载后修改')
    return public_document(survey_view(request.app.state.db, result))

@router.post('/api/surveys/{survey_id}/publish-direct')
def publish_direct(survey_id: str, body: SurveyUpdate, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    if survey['status'] != 'draft' or survey['revision'] != body.revision:
        raise HTTPException(409, '只有草稿可以发布，或版本已更新')
    if not body.questions:
        raise HTTPException(422, '请至少添加一道题目')
    check_deadline(body.deadline, survey['created_at'])
    published_at = now()
    values = body.model_dump(exclude={'revision'})
    values['questions'] = store_questions(request.app.state.db, values['questions'], user, survey['questions'], allow_existing_edits=True)
    result = request.app.state.db.surveys.find_one_and_update(
        {'_id': survey['_id'], 'owner_id': user['_id'], 'status': 'draft', 'revision': body.revision},
        {'$set': {**values, 'status': 'published',
                  'published_at': published_at, 'updated_at': published_at}, '$inc': {'revision': 1}},
        return_document=ReturnDocument.AFTER)
    if not result:
        raise HTTPException(409, '只有草稿可以发布，或版本已更新；请重新加载')
    return {'status': 'published', 'revision': result['revision'], 'share_url': f'/survey/{survey_id}'}

@router.post('/api/surveys/{survey_id}/publish')
def publish(survey_id: str, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    if survey['status'] != 'draft':
        raise HTTPException(409, '只有草稿可以发布')
    try:
        SurveyInput.model_validate({k: survey.get(k) for k in SurveyInput.model_fields})
    except ValidationError as exc:
        raise HTTPException(422, {'message': '问卷配置不合法，不能发布', 'errors': [
            {'question_id': '.'.join(str(x) for x in error['loc']), 'message': error['msg']}
            for error in exc.errors()]})
    if not survey['questions']:
        raise HTTPException(422, '请至少添加一道题目')
    check_deadline(survey['deadline'], survey['created_at'])
    result = request.app.state.db.surveys.update_one(
        {'_id': survey['_id'], 'status': 'draft', 'revision': survey['revision']},
        {'$set': {'status': 'published', 'published_at': now(), 'updated_at': now()}})
    if not result.modified_count:
        raise HTTPException(409, '问卷状态或版本已变化')
    return {'status': 'published', 'share_url': f'/survey/{survey_id}'}

@router.post('/api/surveys/{survey_id}/close')
def close(survey_id: str, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    result = request.app.state.db.surveys.update_one({'_id': survey['_id'], 'status': 'published'},
        {'$set': {'status': 'closed', 'updated_at': now()}})
    if not result.modified_count:
        raise HTTPException(409, '只有已发布问卷可以关闭')
    return {'status': 'closed'}

@router.patch('/api/surveys/{survey_id}/deadline')
def deadline(survey_id: str, body: DeadlineInput, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    check_deadline(body.deadline, survey['created_at'])
    result = request.app.state.db.surveys.update_one({'_id': survey['_id'], 'status': {'$ne': 'closed'}},
        {'$set': {'deadline': body.deadline, 'updated_at': now()}, '$inc': {'revision': 1}})
    if not result.matched_count:
        raise HTTPException(409, '已关闭问卷不能修改截止时间')
    return {'deadline': body.deadline}


@router.post('/api/surveys/{survey_id}/reuse-question', status_code=201)
def reuse(survey_id: str, body: ReuseQuestion, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    raw = db.surveys.find_one({'_id': oid(survey_id), 'owner_id': user['_id']})
    if not raw:
        raise HTTPException(404, '问卷不存在或无权访问')
    if raw['status'] != 'draft' or raw['revision'] != body.revision:
        raise HTTPException(409, '只有草稿可以选题，或问卷版本已更新')
    survey = survey_view(db, raw)
    version = get_ref(db, {'question_id': body.question_id, 'question_version': body.question_version}, user) if body.question_id else get_version(db, body.version_id, user)
    if body.version_id and str(version['_id']) != body.version_id.lower():
        raise HTTPException(422, '来源版本与题目ID/版本不一致')
    question = Question(id=body.local_id, question_id=str(version['question_id']), question_version=version['question_version'],
        source_version_id=str(version['_id']), **version['content']).model_dump()
    data = {k: survey.get(k) for k in SurveyInput.model_fields}
    data['deadline'] = data['deadline'] or now().isoformat()
    data['questions'] = [*survey['questions'], question]
    try:
        SurveyInput.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
    snapshot = store_questions(db, [question], user)[0]
    result = db.surveys.find_one_and_update({'_id': raw['_id'], 'status': 'draft', 'revision': body.revision},
        {'$push': {'questions': snapshot}, '$inc': {'revision': 1}, '$set': {'updated_at': now()}},
        return_document=ReturnDocument.AFTER)
    if not result:
        raise HTTPException(409, '问卷版本已更新')
    return encode(survey_view(db, result))
