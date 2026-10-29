"""Published survey access and version-pinned answer submission."""
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError

from .logic import walk
from .versions import response_query
from .models import ResponseSubmission, Submission
from .security import current_user, now

from .common import oid, public_document
from .surveys import available, shared_survey, survey_state

router = APIRouter(tags=['答卷'])


@router.get('/api/public/surveys/{survey_id}/status')
def public_status(survey_id: str, request: Request, user=Depends(current_user)):
    # A read-only view remains available after closure; response data stays private.
    survey = shared_survey(request, survey_id)
    questions = [{key: question.get(key) for key in
        ('id', 'title', 'type', 'required', 'options', 'validation')}
        for question in survey['questions']]
    return public_document({**{key: survey.get(key) for key in
        ('_id', 'title', 'description', 'status', 'deadline', 'created_at')},
        'questions': questions, **survey_state(survey)})

@router.get('/api/public/surveys/{survey_id}')
def public(survey_id: str, request: Request, user=Depends(current_user)):
    survey = available(request, survey_id)
    return public_document({k: v for k, v in survey.items() if k not in ('owner_id', 'admissions')})

def checked(body, request, survey_id, final):
    survey = available(request, survey_id)
    if body.anonymous and not survey['allow_anonymous']:
        raise HTTPException(422, '该问卷不允许匿名填写')
    result = walk(survey['questions'], body.answers, final=final)
    if result['errors']:
        raise HTTPException(422, {'message': '答案校验失败', 'errors': result['errors']})
    return survey, result

@router.post('/api/public/surveys/{survey_id}/next')
def progress(survey_id: str, body: Submission, request: Request, user=Depends(current_user)):
    _, result = checked(body, request, survey_id, False)
    return result

@router.post('/api/public/surveys/{survey_id}/responses', status_code=201)
def submit(survey_id: str, body: ResponseSubmission, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    survey_oid = oid(survey_id)
    submission_key = hmac.new(request.app.state.settings.app_secret.encode(),
        f"attempt:{survey_oid}:{user['_id']}:{body.submission_id}".encode(), hashlib.sha256).hexdigest()
    payload_hash = hashlib.sha256(json.dumps({'answers': body.answers, 'anonymous': body.anonymous},
        sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

    def replay(existing):
        if not hmac.compare_digest(existing['payload_hash'], payload_hash):
            raise HTTPException(409, '同一次提交的内容已变化，请恢复原提交或重新开始填写')
        return JSONResponse(status_code=200, content={
            'id': str(existing['_id']), 'message': '提交成功', 'replayed': True})

    existing = db.responses.find_one({**response_query(survey_oid), 'submission_key': submission_key})
    if existing:
        return replay(existing)
    survey, result = checked(body, request, survey_id, True)
    admitted_at = now()
    admitted = db.surveys.find_one_and_update(
        {'_id': survey['_id'], 'status': 'published', 'revision': survey['revision'],
         'deadline': {'$gt': admitted_at}},
        {'$inc': {'admissions': 1}})
    if not admitted:
        raise HTTPException(409, '问卷状态、截止时间或版本已变化，请刷新')
    doc = {'survey_document_id': survey['_id'], 'survey_id': survey['survey_id'],
           'survey_version': survey['survey_version'], 'survey_revision': survey['revision'], 'schema_version': 2,
           'answers': result['answers'], 'path': result['path'], 'anonymous': body.anonymous, 'submitted_at': admitted_at,
           'submission_key': submission_key, 'payload_hash': payload_hash}
    if not body.anonymous:
        doc['respondent_id'] = user['_id']
    if not survey['allow_multiple']:
        doc['respondent_key'] = hmac.new(request.app.state.settings.app_secret.encode(),
            f"{survey['_id']}:{user['_id']}".encode(), hashlib.sha256).hexdigest()
    try:
        db.responses.insert_one(doc)
    except DuplicateKeyError:
        existing = db.responses.find_one({**response_query(survey_oid), 'submission_key': submission_key})
        if existing:
            return replay(existing)
        raise HTTPException(409, '该问卷每个账号仅允许提交一次')
    return {'id': str(doc['_id']), 'message': '提交成功', 'replayed': False}

