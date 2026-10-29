"""Exact-version grants; authorization does not implicitly collect a question."""
from fastapi import HTTPException

from .permissions import require_question_author
from .versions import get_ref, key

def target_user(db, username, user):
    target = db.users.find_one({'username': username.lower()})
    if not target:
        raise HTTPException(404, '目标账号不存在')
    if target['_id'] == user['_id']:
        raise HTTPException(422, '无需转发给自己')
    return target


def forwarding_versions(db, items, user):
    identities = [key(item) for item in items]
    if not identities or len(identities) != len(set(identities)):
        raise HTTPException(422, '请选择题目，且不能重复选择相同版本')
    versions = []
    for item in items:
        version = get_ref(db, item, user)
        require_question_author(version, user)
        versions.append(version)
    return versions


def grant_versions(db, versions, target):
    # Complete all authorization checks before making any grants.
    db.questions.update_many({'_id': {'$in': [v['_id'] for v in versions]}},
        {'$addToSet': {'shared_with': target['_id']}})


