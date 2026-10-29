from .versions import response_query

def question_stats(db, survey_id, question):
    qid, kind = question['id'], question['type']
    field = f'answers.{qid}'
    match = {'$match': {**response_query(survey_id), field: {'$exists': True}}}
    count = db.responses.count_documents({**response_query(survey_id), field: {'$exists': True}})
    result = {'question_id': qid, 'title': question['title'], 'type': kind, 'answer_count': count}
    if kind in ('single', 'multiple'):
        pipeline = [match]
        if kind == 'multiple':
            pipeline.append({'$unwind': '$' + field})
        pipeline.append({'$group': {'_id': '$' + field, 'count': {'$sum': 1}}})
        counts = {x['_id']: x['count'] for x in db.responses.aggregate(pipeline)}
        result['options'] = [{**o, 'count': counts.get(o['id'], 0)} for o in question['options']]
    else:
        result['contents'] = [r['answers'][qid] for r in db.responses.find(
            {**response_query(survey_id), field: {'$exists': True}}, {field: 1, '_id': 0}).sort('submitted_at', 1)]
        if kind == 'number':
            rows = list(db.responses.aggregate([match, {'$group': {'_id': None, 'average': {'$avg': '$' + field}}}]))
            result['average'] = rows[0]['average'] if rows else None
    return result
