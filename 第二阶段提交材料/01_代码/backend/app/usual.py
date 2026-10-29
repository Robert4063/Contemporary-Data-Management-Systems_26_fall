"""Reusable question collection and exact-version authorization endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request

from .models import ForwardQuestions, QuestionRef, ShareQuestion
from .security import current_user, now
from .versions import encode, get_ref, get_version, oid, ref, version_view
from .permissions import can_use_question

from .question_service import target_user, forwarding_versions, grant_versions


router = APIRouter(prefix="/api", tags=["常用题目与授权"])


@router.post('/usual-questions', status_code=201)
def collect_question(body: QuestionRef, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    version = get_ref(db, body.model_dump(), user)
    stamp = now()
    db.usual_questions.update_one({'owner_id': user['_id'], **ref(version)},
        {'$setOnInsert': {'revision': 1, 'created_at': stamp},
         '$set': {'in_library': True, 'updated_at': stamp}, '$unset': {'removed_at': ''}}, upsert=True)
    return version_view(db, version)


@router.get('/questions')
def list_questions(request: Request, skip: int = 0, limit: int = 50, user=Depends(current_user)):
    if skip < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, 'skip必须非负，limit为1到100')
    db = request.app.state.db
    entries = list(db.usual_questions.find({'owner_id': user['_id']}))
    query = {'$or': [*[ref(g) for g in entries], {'shared_with': user['_id']}]}
    removed = list(db.usual_questions.find({'owner_id': user['_id'], 'removed_at': {'$exists': True}}))
    if removed:
        query = {'$and': [query, {'$nor': [ref(item) for item in removed]}]}
    available = [v for v in db.questions.find(query).sort('_id', -1) if can_use_question(db, v, user)]
    return {'items': [version_view(db, v) for v in available[skip:skip+limit]], 'total': len(available)}


@router.get('/usual-questions')
def collected_questions(request: Request, skip: int = 0, limit: int = 50, user=Depends(current_user)):
    if skip < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, 'skip必须非负，limit为1到100')
    db = request.app.state.db
    query = {'owner_id': user['_id'], 'in_library': True}
    entries = db.usual_questions.find(query).sort('created_at', -1).skip(skip).limit(limit)
    items = []
    for entry in entries:
        version = db.questions.find_one(ref(entry))
        if can_use_question(db, version, user):
            items.append({**version_view(db, version), 'in_library': True})
        else:
            items.append({**encode(ref(entry)), '_id': str(version['_id']) if version else None,
                'in_library': True, 'unavailable': True})
    return {'items': items, 'total': db.usual_questions.count_documents(query)}


def remove_collected(db, user, query):
    owned = {'owner_id': user['_id'], 'in_library': True, **query}
    entries = list(db.usual_questions.find(owned))
    if not entries:
        raise HTTPException(404, '题目未收录到你的题库')
    db.usual_questions.update_many(owned, {'$set': {'in_library': False, 'updated_at': now(), 'removed_at': now()}, '$inc': {'revision': 1}})
    return {'removed_count': len(entries)}


@router.delete('/usual-questions/{question_id}/{question_version}')
def remove_collected_version(question_id: str, question_version: int, request: Request, user=Depends(current_user)):
    return remove_collected(request.app.state.db, user,
        {'question_id': oid(question_id), 'question_version': question_version})


@router.delete('/usual-questions/{question_id}')
def remove_collected_question(question_id: str, request: Request, user=Depends(current_user)):
    return remove_collected(request.app.state.db, user, {'question_id': oid(question_id)})


@router.post('/questions/forward')
def forward_questions(body: ForwardQuestions, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    target = target_user(db, body.username, user)
    versions = forwarding_versions(db, [item.model_dump() for item in body.items], user)
    grant_versions(db, versions, target)
    return {'recipient': target['username'], 'forwarded_count': len(versions), 'scope': 'exact_versions'}


@router.get('/questions/{version_id}/shares')
def shares(version_id: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    version = get_version(db, version_id, user, owner_only=True)
    return {'items': encode(list(db.users.find({'_id': {'$in': version.get('shared_with', [])}}, {'username': 1})))}


@router.post('/questions/{version_id}/shares')
def share(version_id: str, body: ShareQuestion, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    version = get_version(db, version_id, user, owner_only=True)
    target = target_user(db, body.username, user)
    grant_versions(db, [version], target)
    return {'username': target['username'], 'shared': True, 'scope': 'exact_version'}


@router.delete('/questions/{version_id}/shares/{username}')
def revoke_share(version_id: str, username: str, request: Request, user=Depends(current_user)):
    db = request.app.state.db
    version = get_version(db, version_id, user, owner_only=True)
    target = db.users.find_one({'username': username.lower()})
    if not target:
        raise HTTPException(404, '目标账号不存在')
    db.questions.update_one({'_id': version['_id']}, {'$pull': {'shared_with': target['_id']}})
    return {'username': target['username'], 'shared': False}

