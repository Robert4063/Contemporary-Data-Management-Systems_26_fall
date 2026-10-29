
from fastapi import APIRouter, Depends, HTTPException, Request

from .versions import response_query
from .security import current_user
from .statistics import question_stats

from .surveys import owned

router = APIRouter(tags=['问卷统计'])


@router.get('/api/surveys/{survey_id}/statistics')
def stats(survey_id: str, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    db = request.app.state.db
    return {'survey_id': survey_id, 'response_count': db.responses.count_documents(response_query(survey['_id'])),
            'questions': [question_stats(db, survey['_id'], q) for q in survey['questions']]}

@router.get('/api/surveys/{survey_id}/statistics/{question_id}')
def single_stats(survey_id: str, question_id: str, request: Request, user=Depends(current_user)):
    survey = owned(request, survey_id, user)
    q = next((q for q in survey['questions'] if q['id'] == question_id), None)
    if q is None:
        raise HTTPException(404, '题目不存在')
    return question_stats(request.app.state.db, survey['_id'], q)

