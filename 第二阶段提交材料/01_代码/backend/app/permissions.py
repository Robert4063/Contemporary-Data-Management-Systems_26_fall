"""Shared resource policy: ownership, exact-version use, and author-only grants."""
from bson import ObjectId
from fastapi import HTTPException


def owned_document(collection, identity, user, label):
    doc = collection.find_one({'_id': ObjectId(identity), 'owner_id': user['_id']}) if ObjectId.is_valid(identity) else None
    if doc is None:
        raise HTTPException(404, label+'不存在或无权访问')
    return doc


def can_use_question(db, version, user):
    return bool(version and (version['owner_id'] == user['_id'] or user['_id'] in version.get('shared_with', [])))


def readable_question(db, version, user, owner_only=False):
    if version and (version['owner_id'] == user['_id'] or not owner_only and can_use_question(db, version, user)):
        return version
    raise HTTPException(404, '题目不存在或无权访问')


def require_question_author(version, user):
    if version['owner_id'] != user['_id']:
        raise HTTPException(403, '只有题目作者可以转发；共享接收者只能使用')
    return version
