import hashlib
import hmac
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from bson import ObjectId
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pydantic import ValidationError

from .config import Settings
from .db import initialize
from .logic import walk
from .models import Credentials, DeadlineInput, MetadataUpdate, Question, ResponseSubmission, Submission, SurveyInput, SurveyUpdate
from .security import bearer, current_user, now, password_hash, password_ok, token_hash
from .statistics import question_stats

STATIC = Path(__file__).resolve().parents[2] / 'frontend'


def oid(value):
    if not ObjectId.is_valid(value):
        raise HTTPException(404, '问卷不存在')
    return ObjectId(value)


def public_document(doc):
    return jsonable_encoder(doc, custom_encoder={ObjectId: str})


def check_deadline(deadline, created_at):
    if deadline is None:
        raise HTTPException(422, '截止时间必须填写')
    if deadline is not None and deadline < created_at:
        raise HTTPException(422, '截止时间不得早于问卷创建时间')
    if deadline is not None and deadline <= now():
        raise HTTPException(422, '截止时间必须晚于当前时间')


def create_app(settings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        client = MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=4000)
        try:
            client.admin.command('ping')
            initialize(client[settings.mongo_db])
            app.state.db = client[settings.mongo_db]
            app.state.settings = settings
            yield
        finally:
            client.close()

    app = FastAPI(title='MongoDB 问卷系统 第一阶段', version='1.2.0', lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request, exc):
        # Do not echo secrets or non-JSON floats from malformed input in the error body.
        return JSONResponse(status_code=422, content={'detail': [
            {'loc': list(e['loc']), 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]})

    @app.exception_handler(PyMongoError)
    async def database_error(request, exc):
        logging.getLogger(__name__).error('MongoDB operation failed', exc_info=exc)
        return JSONResponse(status_code=503, content={'detail': '数据库暂时不可用，请稍后重试'})

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.url.path not in ('/docs', '/redoc'):
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        return response

    def owned(request, survey_id, user):
        survey = request.app.state.db.surveys.find_one({'_id': oid(survey_id), 'owner_id': user['_id']})
        if not survey:
            raise HTTPException(404, '问卷不存在或无权访问')
        return survey

    def shared_survey(request, survey_id):
        survey = request.app.state.db.surveys.find_one({'_id': oid(survey_id)})
        if not survey or survey['status'] == 'draft':
            raise HTTPException(404, '问卷不存在或尚未发布')
        return survey

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

    @app.get('/api/health')
    def health(request: Request):
        request.app.state.db.command('ping')
        return {'status': 'ok'}

    @app.post('/api/auth/register', status_code=201)
    def register(body: Credentials, request: Request):
        doc = {'username': body.username.lower(), 'password_hash': password_hash(body.password), 'created_at': now()}
        try:
            request.app.state.db.users.insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(409, '用户名已被注册')
        return {'id': str(doc['_id']), 'username': doc['username']}

    @app.post('/api/auth/login')
    def login(body: Credentials, request: Request):
        db = request.app.state.db
        user = db.users.find_one({'username': body.username.lower()})
        # Perform a hash even for unknown users to reduce username timing differences.
        valid = password_ok(body.password, user['password_hash']) if user else bool(password_hash(body.password)) and False
        if not valid:
            raise HTTPException(401, '用户名或密码错误')
        token = secrets.token_urlsafe(32)
        expires = now() + timedelta(hours=settings.session_hours)
        db.sessions.insert_one({'user_id': user['_id'], 'token_hash': token_hash(token), 'expires_at': expires})
        return {'access_token': token, 'token_type': 'bearer', 'expires_at': expires}

    @app.get('/api/auth/me')
    def me(user=Depends(current_user)):
        return {'id': str(user['_id']), 'username': user['username']}

    @app.post('/api/auth/logout', status_code=204)
    def logout(request: Request, user=Depends(current_user), credentials=Depends(bearer)):
        request.app.state.db.sessions.delete_one({'token_hash': token_hash(credentials.credentials)})

    def insert_draft(data, request, user, incomplete=False):
        created_at = now()
        created_at = created_at.replace(microsecond=created_at.microsecond // 1000 * 1000)
        if not incomplete:
            check_deadline(data.get('deadline'), created_at)
        doc = {**data, 'owner_id': user['_id'], 'status': 'draft', 'revision': 1,
               'schema_version': 1, 'created_at': created_at, 'updated_at': created_at, 'admissions': 0}
        request.app.state.db.surveys.insert_one(doc)
        return public_document(doc)

    @app.post('/api/surveys', status_code=201)
    def create(body: SurveyInput, request: Request, user=Depends(current_user)):
        return insert_draft(body.model_dump(), request, user)

    @app.post('/api/surveys/drafts', status_code=201)
    def begin_draft(request: Request, user=Depends(current_user)):
        # The create-button request reaches the server before editing begins.
        return insert_draft({'title': '未命名问卷', 'description': '', 'allow_anonymous': False,
            'allow_multiple': False, 'deadline': None, 'questions': []}, request, user, incomplete=True)

    @app.get('/api/surveys')
    def listing(request: Request, skip: int = 0, limit: int = 50, user=Depends(current_user)):
        if skip < 0 or not 1 <= limit <= 100:
            raise HTTPException(422, 'skip 必须非负，limit 必须为 1 到 100')
        db = request.app.state.db
        query = {'owner_id': user['_id']}
        return {'items': public_document(list(db.surveys.find(query, {'questions': 0}).sort('created_at', -1).skip(skip).limit(limit))),
                'total': db.surveys.count_documents(query)}

    @app.get('/api/surveys/{survey_id}')
    def detail(survey_id: str, request: Request, user=Depends(current_user)):
        return public_document(owned(request, survey_id, user))

    @app.put('/api/surveys/{survey_id}')
    def edit(survey_id: str, body: SurveyUpdate, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        check_deadline(body.deadline, survey['created_at'])
        values = body.model_dump(exclude={'revision'})
        result = request.app.state.db.surveys.find_one_and_update(
            {'_id': survey['_id'], 'status': 'draft', 'revision': body.revision},
            {'$set': {**values, 'updated_at': now()}, '$inc': {'revision': 1}}, return_document=ReturnDocument.AFTER)
        if not result:
            raise HTTPException(409, '仅草稿可以编辑，或版本已更新；请重新加载')
        return public_document(result)

    @app.post('/api/surveys/{survey_id}/questions', status_code=201)
    def add_question(survey_id: str, body: Question, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        data = {k: survey[k] for k in SurveyInput.model_fields}
        data['questions'] = [*survey['questions'], body.model_dump()]
        try:
            SurveyInput.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(422, str(exc))
        result = request.app.state.db.surveys.find_one_and_update(
            {'_id': survey['_id'], 'status': 'draft', 'revision': survey['revision']},
            {'$push': {'questions': body.model_dump()}, '$inc': {'revision': 1}, '$set': {'updated_at': now()}},
            return_document=ReturnDocument.AFTER)
        if not result:
            raise HTTPException(409, '仅草稿可以添加题目，或版本已更新')
        return public_document(result)

    @app.patch('/api/surveys/{survey_id}/metadata')
    def metadata(survey_id: str, body: MetadataUpdate, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        result = request.app.state.db.surveys.find_one_and_update(
            {'_id': survey['_id'], 'revision': body.revision},
            {'$set': {'title': body.title, 'description': body.description, 'updated_at': now()},
             '$inc': {'revision': 1}}, return_document=ReturnDocument.AFTER)
        if not result:
            raise HTTPException(409, '问卷版本已更新，请重新加载后修改')
        return public_document(result)

    @app.post('/api/surveys/{survey_id}/publish-direct')
    def publish_direct(survey_id: str, body: SurveyUpdate, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        if not body.questions:
            raise HTTPException(422, '请至少添加一道题目')
        check_deadline(body.deadline, survey['created_at'])
        published_at = now()
        result = request.app.state.db.surveys.find_one_and_update(
            {'_id': survey['_id'], 'owner_id': user['_id'], 'status': 'draft', 'revision': body.revision},
            {'$set': {**body.model_dump(exclude={'revision'}), 'status': 'published',
                      'published_at': published_at, 'updated_at': published_at}, '$inc': {'revision': 1}},
            return_document=ReturnDocument.AFTER)
        if not result:
            raise HTTPException(409, '只有草稿可以发布，或版本已更新；请重新加载')
        return {'status': 'published', 'revision': result['revision'], 'share_url': f'/survey/{survey_id}'}

    @app.post('/api/surveys/{survey_id}/publish')
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

    @app.post('/api/surveys/{survey_id}/close')
    def close(survey_id: str, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        result = request.app.state.db.surveys.update_one({'_id': survey['_id'], 'status': 'published'},
            {'$set': {'status': 'closed', 'updated_at': now()}})
        if not result.modified_count:
            raise HTTPException(409, '只有已发布问卷可以关闭')
        return {'status': 'closed'}

    @app.patch('/api/surveys/{survey_id}/deadline')
    def deadline(survey_id: str, body: DeadlineInput, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        check_deadline(body.deadline, survey['created_at'])
        result = request.app.state.db.surveys.update_one({'_id': survey['_id'], 'status': {'$ne': 'closed'}},
            {'$set': {'deadline': body.deadline, 'updated_at': now()}, '$inc': {'revision': 1}})
        if not result.matched_count:
            raise HTTPException(409, '已关闭问卷不能修改截止时间')
        return {'deadline': body.deadline}

    @app.get('/api/public/surveys/{survey_id}/status')
    def public_status(survey_id: str, request: Request, user=Depends(current_user)):
        # A read-only view remains available after closure; response data stays private.
        survey = shared_survey(request, survey_id)
        questions = [{key: question.get(key) for key in
            ('id', 'title', 'type', 'required', 'options', 'validation')}
            for question in survey['questions']]
        return public_document({**{key: survey.get(key) for key in
            ('_id', 'title', 'description', 'status', 'deadline', 'created_at')},
            'questions': questions, **survey_state(survey)})

    @app.get('/api/public/surveys/{survey_id}')
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

    @app.post('/api/public/surveys/{survey_id}/next')
    def progress(survey_id: str, body: Submission, request: Request, user=Depends(current_user)):
        _, result = checked(body, request, survey_id, False)
        return result

    @app.post('/api/public/surveys/{survey_id}/responses', status_code=201)
    def submit(survey_id: str, body: ResponseSubmission, request: Request, user=Depends(current_user)):
        db = request.app.state.db
        survey_oid = oid(survey_id)
        submission_key = hmac.new(settings.app_secret.encode(),
            f"attempt:{survey_oid}:{user['_id']}:{body.submission_id}".encode(), hashlib.sha256).hexdigest()
        payload_hash = hashlib.sha256(json.dumps({'answers': body.answers, 'anonymous': body.anonymous},
            sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

        def replay(existing):
            if not hmac.compare_digest(existing['payload_hash'], payload_hash):
                raise HTTPException(409, '同一次提交的内容已变化，请恢复原提交或重新开始填写')
            return JSONResponse(status_code=200, content={
                'id': str(existing['_id']), 'message': '提交成功', 'replayed': True})

        existing = db.responses.find_one({'survey_id': survey_oid, 'submission_key': submission_key})
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
        doc = {'survey_id': survey['_id'], 'survey_revision': survey['revision'], 'schema_version': 1,
               'answers': result['answers'], 'path': result['path'], 'anonymous': body.anonymous, 'submitted_at': admitted_at,
               'submission_key': submission_key, 'payload_hash': payload_hash}
        if not body.anonymous:
            doc['respondent_id'] = user['_id']
        if not survey['allow_multiple']:
            doc['respondent_key'] = hmac.new(settings.app_secret.encode(),
                f"{survey['_id']}:{user['_id']}".encode(), hashlib.sha256).hexdigest()
        try:
            db.responses.insert_one(doc)
        except DuplicateKeyError:
            existing = db.responses.find_one({'survey_id': survey_oid, 'submission_key': submission_key})
            if existing:
                return replay(existing)
            raise HTTPException(409, '该问卷每个账号仅允许提交一次')
        return {'id': str(doc['_id']), 'message': '提交成功', 'replayed': False}

    @app.get('/api/surveys/{survey_id}/statistics')
    def stats(survey_id: str, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        db = request.app.state.db
        return {'survey_id': survey_id, 'response_count': db.responses.count_documents({'survey_id': survey['_id']}),
                'questions': [question_stats(db, survey['_id'], q) for q in survey['questions']]}

    @app.get('/api/surveys/{survey_id}/statistics/{question_id}')
    def single_stats(survey_id: str, question_id: str, request: Request, user=Depends(current_user)):
        survey = owned(request, survey_id, user)
        q = next((q for q in survey['questions'] if q['id'] == question_id), None)
        if q is None:
            raise HTTPException(404, '题目不存在')
        return question_stats(request.app.state.db, survey['_id'], q)

    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/', include_in_schema=False)
    @app.get('/survey/{survey_id}', include_in_schema=False)
    def page():
        return FileResponse(STATIC / 'index.html')

    return app


app = create_app()
