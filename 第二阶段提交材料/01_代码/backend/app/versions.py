"""Canonical question pairs, embedded snapshots, and first-stage data upgrades."""
from copy import deepcopy
import hashlib

from bson import ObjectId
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pymongo.errors import DuplicateKeyError

from .models import LibraryQuestion
from .permissions import readable_question as accessible
from .security import now

CONTENT_FIELDS = tuple(LibraryQuestion.model_fields)


def encode(value):
    return jsonable_encoder(value, custom_encoder={ObjectId: str})


def oid(value):
    if not ObjectId.is_valid(value):
        raise HTTPException(404, '资源不存在或无权访问')
    return ObjectId(value)


def ref(version):
    return {'question_id': version['question_id'], 'question_version': version['question_version']}


def key(item):
    return str(item['question_id']), item['question_version']


def content(question):
    return LibraryQuestion.model_validate({k: question[k] for k in CONTENT_FIELDS}).model_dump()


def get_version(db, version_id, user, owner_only=False):
    return accessible(db, db.questions.find_one({'_id': oid(version_id)}), user, owner_only)


def get_ref(db, item, user, owner_only=False):
    query = {'question_id': oid(str(item['question_id'])), 'question_version': item['question_version']}
    return accessible(db, db.questions.find_one(query), user, owner_only)


def insert_question(db, values, user, source=None, restored=False):
    values = LibraryQuestion.model_validate(values).model_dump()
    same_author = source is not None and source['owner_id'] == user['_id']
    logical_id = source['question_id'] if same_author else ObjectId()
    for _ in range(40):
        latest = db.questions.find_one({'question_id': logical_id}, sort=[('question_version', -1)])
        number = latest['question_version']+1 if latest else 1
        doc = {'question_id': logical_id, 'question_version': number, 'owner_id': user['_id'],
            'shared_with': [],
            'parent': ref(source) if source else None,
            'restored_from': ref(source) if restored else None,
            'content': values, 'created_at': now()}
        try:
            db.questions.insert_one(doc)
            return doc
        except DuplicateKeyError:
            continue
    raise HTTPException(409, '并发版本创建较多，请重试')


def version_view(db, doc):
    """Expose explicit pairs; legacy aliases are HTTP-only and are never stored."""
    parent = db.questions.find_one(doc['parent'], {'_id': 1}) if doc.get('parent') else None
    restored = db.questions.find_one(doc['restored_from'], {'_id': 1}) if doc.get('restored_from') else None
    public = {k: v for k, v in doc.items() if k != 'shared_with'}
    return encode({**public, 'version': doc['question_version'], 'root_id': doc['question_id'],
        'parent_id': parent['_id'] if parent else None,
        'restored_from': restored['_id'] if restored else None,
        'restored_from_ref': doc.get('restored_from')})


def survey_view(db, doc):
    if doc is None:
        return None
    result = deepcopy(doc)
    result.setdefault('survey_id', result['_id'])
    result.setdefault('survey_version', 1)
    flat = []
    for item in result.get('questions', []):
        if 'content' not in item:
            flat.append(item)
            continue
        v = db.questions.find_one({'question_id': item['question_id'], 'question_version': item['question_version']})
        reusable = db.usual_questions.find_one({'question_id': item['question_id'], 'question_version': item['question_version']})
        flat.append({'id': item['id'], **item['content'], 'question_id': str(item['question_id']),
            'question_version': item['question_version'], 'source_version_id': str(v['_id']) if v and reusable else None,
            'jump_enabled': item.get('jump_enabled', False), 'rules': item.get('rules', [])})
    result['questions'] = flat
    return result


def store_questions(db, questions, user, existing=(), *, allow_existing_edits=False):
    prior = {q['id']: q for q in existing}
    stored = []
    for q in questions:
        values = content(q)
        old = prior.get(q['id'])
        v = None
        if q.get('source_version_id'):
            v = db.questions.find_one({'_id': oid(q['source_version_id'])})
            if not v:
                raise HTTPException(404, '题目版本不存在')
            if q.get('question_id') and key(q) != key(v):
                raise HTTPException(422, '来源版本与题目ID/版本不一致')
        elif q.get('question_id'):
            v = db.questions.find_one({'question_id': oid(q['question_id']), 'question_version': q['question_version']})
            if not v:
                raise HTTPException(404, '题目版本不存在')
        elif old and old.get('question_id'):
            v = db.questions.find_one({'question_id': oid(old['question_id']), 'question_version': old['question_version']})
        if v:
            existing_source = bool(old and old.get('question_id') and key(old) == key(v) and content(old) == v['content'])
            retained = old and old.get('question_id') and key(old) == key(v) and content(old) == values
            editing_copy = allow_existing_edits and existing_source
            if not retained and not editing_copy:
                accessible(db, v, user)
            if values != v['content']:
                if not editing_copy and (q.get('source_version_id') or v['owner_id'] != user['_id'] or db.usual_questions.find_one(ref(v))):
                    raise HTTPException(422, '引用题目内容固定，请先创建题目新版本')
                # Change the owned draft's reference, never the immutable source.
                # Another author's source becomes a separately owned question.
                v = insert_question(db, values, user, v)
        else:
            v = insert_question(db, values, user)
        stored.append({'id': q['id'], **ref(v), 'content': values,
            'jump_enabled': q.get('jump_enabled', False), 'rules': q.get('rules', [])})
    return stored


def response_query(survey_document_id):
    return {'$or': [{'survey_document_id': survey_document_id},
        {'survey_document_id': {'$exists': False}, 'survey_id': survey_document_id}]}


def upgrade_existing(db):
    """Idempotent, additive conversion; legacy experimental collections are not deleted."""
    names = set(db.list_collection_names())
    if 'question_versions' in names:
        for old in db.question_versions.find():
            family = db.question_families.find_one({'_id': old['root_id']}) if 'question_families' in names else None
            parent_old = db.question_versions.find_one({'_id': old.get('parent_id')}) if old.get('parent_id') else None
            restored_old = db.question_versions.find_one({'_id': old.get('restored_from')}) if old.get('restored_from') else None
            old_grant = db.usual_questions.find_one({'owner_id': old['owner_id'], 'question_id': old['root_id'], 'question_version': old['version']})
            doc = {'_id': old['_id'], 'question_id': old['root_id'], 'question_version': old['version'],
                'owner_id': old['owner_id'], 'content': LibraryQuestion.model_validate(old['content']).model_dump(), 'created_at': old['created_at'],
                'shared_with': old_grant.get('shared_with', []) if old_grant else family.get('shared_with', []) if family else [],
                'parent': {'question_id': parent_old['root_id'], 'question_version': parent_old['version']} if parent_old else None,
                'restored_from': {'question_id': restored_old['root_id'], 'question_version': restored_old['version']} if restored_old else None}
            db.questions.update_one({'_id': old['_id']}, {'$setOnInsert': doc}, upsert=True)
    for survey in db.surveys.find({'survey_version': {'$exists': False}}):
        snapshots = []
        for q in survey['questions']:
            v = db.questions.find_one({'_id': oid(q['source_version_id'])}) if q.get('source_version_id') else None
            if not v:
                identity = ObjectId(hashlib.sha256(f"legacy:{survey['_id']}:{q['id']}".encode()).hexdigest()[:24])
                v = {'question_id': identity, 'question_version': 1, 'owner_id': survey['owner_id'],
                    'content': content(q), 'parent': None, 'restored_from': None, 'created_at': survey['created_at']}
                db.questions.update_one(ref(v), {'$setOnInsert': v}, upsert=True)
            snapshots.append({'id': q['id'], **ref(v), 'content': content(q),
                'jump_enabled': q.get('jump_enabled', bool(q.get('rules'))), 'rules': q.get('rules', [])})
        db.surveys.update_one({'_id': survey['_id'], 'survey_version': {'$exists': False}},
            {'$set': {'survey_id': survey['_id'], 'survey_version': 1, 'parent_version': None, 'questions': snapshots, 'schema_version': 2}})
    for response in db.responses.find({'survey_document_id': {'$exists': False}}):
        survey = db.surveys.find_one({'_id': response['survey_id']})
        if survey:
            db.responses.update_one({'_id': response['_id']}, {'$set': {
                'survey_document_id': survey['_id'], 'survey_version': survey.get('survey_version', 1),
                'survey_id': survey.get('survey_id', survey['_id']), 'schema_version': 2}})
    # Old rows mix historical automatic entries and grants. Keep the content and
    # permissions, but require a new explicit collection action for membership.
    for version in db.questions.find({'shared_with': {'$exists': False}}):
        old_grant = db.usual_questions.find_one({'owner_id': version['owner_id'], **ref(version)})
        db.questions.update_one({'_id': version['_id'], 'shared_with': {'$exists': False}},
            {'$set': {'shared_with': old_grant.get('shared_with', []) if old_grant else []}})
    db.usual_questions.update_many({'in_library': {'$exists': False}}, {'$set': {'in_library': False}})
