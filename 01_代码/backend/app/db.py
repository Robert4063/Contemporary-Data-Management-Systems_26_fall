"""MongoDB collection validators and indexes; initialize idempotently at startup."""
from pymongo import ASCENDING, DESCENDING

SCHEMAS = {
    'users': {'required': ['username', 'password_hash', 'created_at'], 'properties': {
        'username': {'bsonType': 'string'}, 'password_hash': {'bsonType': 'string'}, 'created_at': {'bsonType': 'date'}}},
    'sessions': {'required': ['user_id', 'token_hash', 'expires_at'], 'properties': {
        'user_id': {'bsonType': 'objectId'}, 'token_hash': {'bsonType': 'string'}, 'expires_at': {'bsonType': 'date'}}},
    'surveys': {'required': ['owner_id', 'title', 'status', 'questions', 'revision', 'schema_version', 'created_at'], 'properties': {
        'owner_id': {'bsonType': 'objectId'}, 'title': {'bsonType': 'string'},
        'status': {'enum': ['draft', 'published', 'closed']}, 'questions': {'bsonType': 'array'},
        'revision': {'bsonType': 'int'}, 'schema_version': {'bsonType': 'int'}, 'created_at': {'bsonType': 'date'},
        'deadline': {'bsonType': ['date', 'null']}, 'allow_anonymous': {'bsonType': 'bool'}, 'allow_multiple': {'bsonType': 'bool'}}},
    'responses': {'required': ['survey_id', 'survey_revision', 'answers', 'path', 'anonymous', 'submitted_at', 'schema_version'], 'properties': {
        'survey_id': {'bsonType': 'objectId'}, 'survey_revision': {'bsonType': 'int'}, 'answers': {'bsonType': 'object'},
        'path': {'bsonType': 'array'}, 'anonymous': {'bsonType': 'bool'}, 'submitted_at': {'bsonType': 'date'},
        'respondent_id': {'bsonType': 'objectId'}, 'respondent_key': {'bsonType': 'string'},
        'submission_key': {'bsonType': 'string'}, 'payload_hash': {'bsonType': 'string'}}},
}


def initialize(db):
    existing = db.list_collection_names()
    for name, schema in SCHEMAS.items():
        validator = {'$jsonSchema': {'bsonType': 'object', **schema}}
        if name not in existing:
            db.create_collection(name, validator=validator)
        else:
            db.command('collMod', name, validator=validator, validationLevel='strict')
    db.users.create_index('username', unique=True)
    db.sessions.create_index('token_hash', unique=True)
    db.sessions.create_index('expires_at', expireAfterSeconds=0)
    db.surveys.create_index([('owner_id', ASCENDING), ('created_at', DESCENDING)])
    db.responses.create_index([('survey_id', ASCENDING), ('submitted_at', DESCENDING)])
    db.responses.create_index([('survey_id', ASCENDING), ('respondent_key', ASCENDING)], unique=True,
                              partialFilterExpression={'respondent_key': {'$type': 'string'}})
    db.responses.create_index([('survey_id', ASCENDING), ('submission_key', ASCENDING)], unique=True,
                              partialFilterExpression={'submission_key': {'$type': 'string'}})
