"""Six active collections; reusable entries store individual question versions."""
from pymongo import ASCENDING, DESCENDING

SCHEMAS = {
    'users': {'required': ['username', 'password_hash', 'created_at'], 'properties': {
        'username': {'bsonType': 'string'}, 'password_hash': {'bsonType': 'string'}, 'created_at': {'bsonType': 'date'}}},
    'sessions': {'required': ['user_id', 'token_hash', 'expires_at'], 'properties': {
        'user_id': {'bsonType': 'objectId'}, 'token_hash': {'bsonType': 'string'}, 'expires_at': {'bsonType': 'date'}}},
    'questions': {'required': ['question_id', 'question_version', 'owner_id', 'content', 'created_at'], 'properties': {
        'question_id': {'bsonType': 'objectId'}, 'question_version': {'bsonType': 'int', 'minimum': 1},
        'owner_id': {'bsonType': 'objectId'}, 'parent': {'bsonType': ['object', 'null']},
        'shared_with': {'bsonType': 'array', 'items': {'bsonType': 'objectId'}},
        'restored_from': {'bsonType': ['object', 'null']},
        'content': {'bsonType': 'object', 'required': ['title', 'type', 'required', 'options', 'validation'],
            'properties': {'title': {'bsonType': 'string'}, 'type': {'enum': ['single', 'multiple', 'text', 'number']},
                'required': {'bsonType': 'bool'}, 'options': {'bsonType': 'array'}, 'validation': {'bsonType': 'object'}}},
        'created_at': {'bsonType': 'date'}}},
    'usual_questions': {'required': ['owner_id', 'question_id', 'question_version', 'revision', 'created_at'], 'properties': {
        'owner_id': {'bsonType': 'objectId'}, 'question_id': {'bsonType': 'objectId'},
        'question_version': {'bsonType': 'int', 'minimum': 1},
        'in_library': {'bsonType': 'bool'},
        'removed_at': {'bsonType': 'date'},
        'revision': {'bsonType': 'int'}, 'created_at': {'bsonType': 'date'}}},
    'surveys': {'required': ['survey_id', 'survey_version', 'owner_id', 'title', 'status', 'questions', 'revision', 'schema_version', 'created_at'], 'properties': {
        'survey_id': {'bsonType': 'objectId'}, 'survey_version': {'bsonType': 'int', 'minimum': 1},
        'parent_version': {'bsonType': ['int', 'null']},
        'owner_id': {'bsonType': 'objectId'}, 'title': {'bsonType': 'string'},
        'status': {'enum': ['draft', 'published', 'closed']}, 'questions': {'bsonType': 'array'},
        'revision': {'bsonType': 'int'}, 'schema_version': {'bsonType': 'int'}, 'created_at': {'bsonType': 'date'},
        'deadline': {'bsonType': ['date', 'null']}, 'allow_anonymous': {'bsonType': 'bool'}, 'allow_multiple': {'bsonType': 'bool'}}},
    'responses': {'required': ['survey_document_id', 'survey_id', 'survey_version', 'survey_revision', 'answers', 'path', 'anonymous', 'submitted_at', 'schema_version'], 'properties': {
        'survey_id': {'bsonType': 'objectId'}, 'survey_document_id': {'bsonType': 'objectId'},
        'survey_version': {'bsonType': 'int', 'minimum': 1},
        'survey_revision': {'bsonType': 'int'}, 'answers': {'bsonType': 'object'}, 'path': {'bsonType': 'array'},
        'anonymous': {'bsonType': 'bool'}, 'submitted_at': {'bsonType': 'date'},
        'respondent_id': {'bsonType': 'objectId'}, 'respondent_key': {'bsonType': 'string'},
        'submission_key': {'bsonType': 'string'}, 'payload_hash': {'bsonType': 'string'}}},
}


def initialize(db):
    existing = set(db.list_collection_names())
    for name, schema in SCHEMAS.items():
        validator = {'$jsonSchema': {'bsonType': 'object', **schema}}
        if name not in existing:
            db.create_collection(name, validator=validator)
        else:
            db.command('collMod', name, validator=validator, validationLevel='strict')
    db.users.create_index('username', unique=True)
    db.sessions.create_index('token_hash', unique=True)
    db.sessions.create_index('expires_at', expireAfterSeconds=0)
    db.questions.create_index([('question_id', ASCENDING), ('question_version', ASCENDING)], unique=True)
    db.questions.create_index([('owner_id', ASCENDING), ('created_at', DESCENDING)])
    db.usual_questions.create_index([('owner_id', ASCENDING), ('question_id', ASCENDING), ('question_version', ASCENDING)], unique=True)
    db.questions.create_index('shared_with')
    db.surveys.create_index([('owner_id', ASCENDING), ('created_at', DESCENDING)])
    db.surveys.create_index([('survey_id', ASCENDING), ('survey_version', ASCENDING)], unique=True,
        partialFilterExpression={'survey_id': {'$type': 'objectId'}})
    # Several versions may remain editable drafts in the same survey family.
    for index in db.surveys.list_indexes():
        if (index.get('unique') and list(index['key'].items()) == [('survey_id', ASCENDING), ('status', ASCENDING)]
                and index.get('partialFilterExpression', {}).get('status') == 'draft'):
            db.surveys.drop_index(index['name'])
    db.surveys.create_index([('questions.question_id', ASCENDING), ('questions.question_version', ASCENDING)])
    db.responses.create_index([('survey_document_id', ASCENDING), ('submitted_at', DESCENDING)])
    db.responses.create_index([('survey_document_id', ASCENDING), ('respondent_key', ASCENDING)], unique=True,
        partialFilterExpression={'respondent_key': {'$type': 'string'}, 'survey_document_id': {'$type': 'objectId'}})
    db.responses.create_index([('survey_document_id', ASCENDING), ('submission_key', ASCENDING)], unique=True,
        partialFilterExpression={'submission_key': {'$type': 'string'}, 'survey_document_id': {'$type': 'objectId'}})
    from .versions import upgrade_existing
    upgrade_existing(db)
    # The old key now contains a logical ID shared by survey versions. Replace
    # only the two obsolete uniqueness constraints after canonical migration.
    old_keys = {
        (('survey_id', ASCENDING), ('respondent_key', ASCENDING)),
        (('survey_id', ASCENDING), ('submission_key', ASCENDING)),
    }
    for index in db.responses.list_indexes():
        if index.get('unique') and tuple(index['key'].items()) in old_keys:
            db.responses.drop_index(index['name'])
