"""Question content, immutable versions and version-scoped discovery."""
from fastapi import APIRouter, Depends, HTTPException, Request

from .models import LibraryQuestion
from .security import current_user
from .versions import accessible, encode, get_ref, get_version, insert_question, oid, ref, version_view

from .permissions import can_use_question

router = APIRouter(prefix="/api", tags=["题目版本"])


@router.get('/questions/{question_id}/versions')
def available_versions(question_id: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    versions = [version_view(db, version) for version in db.questions.find(
        {'question_id': oid(question_id)}).sort('question_version', 1) if can_use_question(db, version, user)]
    if not versions:
        raise HTTPException(404, '题目不存在或无权访问')
    return {'question_id': question_id.lower(), 'items': versions}


@router.post('/questions', status_code=201)
def create_question(body: LibraryQuestion, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    return version_view(db, insert_question(db, body.model_dump(), user))


@router.get('/questions/by-key/{question_id}/{question_version}')
def question_by_key(question_id: str, question_version: int, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    return version_view(db, get_ref(db, {'question_id': question_id, 'question_version': question_version}, user))


@router.get('/questions/{version_id}')
def get_question(version_id: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    return version_view(db, get_version(db, version_id, user))


@router.post('/questions/{version_id}/versions', status_code=201)
def new_version(version_id: str, body: LibraryQuestion, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    return version_view(db, insert_question(db, body.model_dump(), user, get_version(db, version_id, user)))


@router.post('/questions/{version_id}/restore', status_code=201)
def restore_version(version_id: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    source = get_version(db, version_id, user)
    return version_view(db, insert_question(db, source['content'], user, source, restored=True))


@router.get('/questions/{version_id}/history')
def version_history(version_id: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    source = get_version(db, version_id, user)
    versions = []
    for version in db.questions.find({'question_id': source['question_id']}).sort('question_version', 1):
        try:
            accessible(db, version, user)
            versions.append(version_view(db, version))
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    return {'question_id': str(source['question_id']), 'root_id': str(source['question_id']), 'items': versions}


def usage_filter(source):
    return {'questions': {'$elemMatch': ref(source)}}


@router.get('/questions/{version_id}/usages')
def usages(version_id: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    source = get_version(db, version_id, user)
    query = usage_filter(source)
    items = []
    for survey in db.surveys.find({**query, 'owner_id': user['_id']}, {'title': 1, 'status': 1, 'questions': 1, 'survey_id': 1, 'survey_version': 1}):
        items.append({'survey_id': str(survey['_id']), 'logical_survey_id': str(survey.get('survey_id', survey['_id'])),
            'survey_version': survey.get('survey_version', 1), 'title': survey['title'], 'status': survey['status'],
            'local_ids': [q['id'] for q in survey['questions'] if q.get('question_id') == source['question_id'] and q.get('question_version') == source['question_version']]})
    total = db.surveys.count_documents(query) if source['owner_id'] == user['_id'] else len(items)
    return {'version_id': version_id, **encode(ref(source)), 'items': items, 'visible_survey_count': len(items),
        'total_survey_count': total, 'scope': 'own_surveys'}


@router.get('/questions/{version_id}/statistics')
def cross_statistics(version_id: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    source = get_version(db, version_id, user)
    query = {**usage_filter(source), 'owner_id': user['_id']}
    pipeline = [
        {'$match': query}, {'$unwind': '$questions'},
        {'$match': {'questions.question_id': source['question_id'], 'questions.question_version': source['question_version']}},
        {'$lookup': {'from': 'responses', 'localField': '_id', 'foreignField': 'survey_document_id', 'as': 'response'}},
        {'$unwind': '$response'},
        {'$project': {'submitted_at': '$response.submitted_at', 'response_id': '$response._id',
            'entries': {'$filter': {'input': {'$objectToArray': '$response.answers'}, 'as': 'entry',
                'cond': {'$eq': ['$$entry.k', '$questions.id']}}}}},
        {'$unwind': '$entries'}, {'$match': {'entries.v': {'$ne': None}}},
        {'$project': {'value': '$entries.v', 'submitted_at': 1, 'response_id': 1}},
    ]
    content = source['content']
    summary = [{'$group': {'_id': None, 'answer_count': {'$sum': 1}, 'responses': {'$addToSet': '$response_id'}, 'average': {'$avg': '$value'}}}]
    distribution = ([{'$unwind': '$value'}] if content['type'] == 'multiple' else []) + [
        {'$group': {'_id': '$value', 'count': {'$sum': 1}}}, {'$sort': {'_id': 1}}]
    facets = {'summary': summary, 'distribution': distribution}
    if content['type'] in ('text', 'number'):
        facets['contents'] = [{'$sort': {'submitted_at': 1, 'response_id': 1}}, {'$project': {'_id': 0, 'value': 1}}]
    row = next(db.surveys.aggregate([*pipeline, {'$facet': facets}]))
    totals = row['summary'][0] if row['summary'] else {}
    result = {'version_id': version_id, **encode(ref(source)), 'root_id': str(source['question_id']), 'version': source['question_version'],
        'title': content['title'], 'type': content['type'], 'scope': 'own_surveys', 'survey_count': db.surveys.count_documents(query),
        'answer_count': totals.get('answer_count', 0), 'response_count': len(totals.get('responses', []))}
    if content['type'] in ('single', 'multiple'):
        counts = {v['_id']: v['count'] for v in row['distribution']}
        result['options'] = [{**o, 'count': counts.get(o['id'], 0)} for o in content['options']]
    else:
        result['contents'] = [entry['value'] for entry in row['contents']]
        if content['type'] == 'number':
            result['average'] = totals.get('average')
            result['distribution'] = [{'value': v['_id'], 'count': v['count']} for v in row['distribution']]
    return result


