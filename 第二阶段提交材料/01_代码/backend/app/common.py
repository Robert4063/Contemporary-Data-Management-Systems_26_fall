from bson import ObjectId
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from .security import now


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


