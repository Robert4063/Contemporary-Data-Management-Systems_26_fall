import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .config import Settings
from .db import initialize
from .accounts import router as account_router
from .surveys import router as survey_router
from .responses import router as response_router
from .survey_stats import router as stats_router
from .questions import router as question_router
from .usual import router as usual_router

STATIC = Path(__file__).resolve().parents[2] / 'frontend'


def create_app(settings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        client = MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=4000)
        try:
            client.admin.command('ping')
            initialize(client[settings.mongo_db])
            app.state.db = client[settings.mongo_db]
            app.state.settings = settings
            yield
        finally:
            client.close()

    app = FastAPI(title='MongoDB 问卷系统 第二阶段', version='3.0.0', lifespan=lifespan)
    for router in (account_router, survey_router, response_router, stats_router, question_router, usual_router):
        app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request, exc):
        # Do not echo secrets or non-JSON floats from malformed input in the error body.
        return JSONResponse(status_code=422, content={'detail': [
            {'loc': list(e['loc']), 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]})

    @app.exception_handler(PyMongoError)
    async def database_error(request, exc):
        logging.getLogger(__name__).error('MongoDB operation failed', exc_info=exc)
        return JSONResponse(status_code=503, content={'detail': '数据库暂时不可用，请稍后重试'})

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if request.url.path not in ('/docs', '/redoc'):
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/health')
    def health(request: Request):
        request.app.state.db.command('ping')
        return {'status': 'ok'}

    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/', include_in_schema=False)
    @app.get('/survey/{survey_id}', include_in_schema=False)
    def page():
        return FileResponse(STATIC / 'index.html')

    return app


app = create_app()
