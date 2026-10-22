import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from review.storage import Store
from review.documents import parse_document, MAX_BYTES
from review.model import Model, ModelError
from review.graph import build_graph
from review.summary import SummaryCache, CITATION

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / '.env')


class TextDocument(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=15, max_length=1_000_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] = Field(min_length=1, max_length=20)
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    mode: Literal['auto', 'ask', 'summary', 'quiz'] = 'auto'


def create_app(db_path=None, model=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = Store(db_path or os.getenv('REVIEW_DB', str(HERE / 'data' / 'review.db')))
        app.state.model = model or Model()
        app.state.progress = {}
        app.state.graph = build_graph(app.state.model, SummaryCache(app.state.store.path),
            lambda request_id, text: app.state.progress.__setitem__(request_id, text) if request_id in app.state.progress else None)
        app.state.locks = {}
        yield

    app = FastAPI(title='다시봄 · 수업 복습 에이전트', lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=HERE/'web'), name='static')

    @app.get('/')
    def index():
        return FileResponse(HERE/'web'/'index.html')

    @app.get('/api/health')
    def health():
        backend = app.state.model.backend
        return {'status': 'ok', 'backend': backend,
                'model': app.state.model.name if backend != 'extractive' else None,
                'label': '원문 발췌 모드' if backend == 'extractive' else f'{backend} AI 모드'}

    @app.get('/api/documents')
    def documents():
        return app.state.store.documents()

    @app.post('/api/documents/upload')
    async def upload(file: UploadFile):
        payload = await file.read(MAX_BYTES + 1)
        await file.close()
        try:
            name, chunks = await asyncio.to_thread(parse_document, file.filename or '자료.txt', payload)
            return app.state.store.add(name, payload, chunks)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/documents/text')
    def text_document(req: TextDocument):
        name = req.title if req.title.lower().endswith(('.txt','.md')) else req.title + '.txt'
        payload = req.text.encode('utf-8')
        try:
            name, chunks = parse_document(name, payload)
            return app.state.store.add(name, payload, chunks)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete('/api/documents/{doc_id}')
    def delete(doc_id: str):
        if not app.state.store.delete(doc_id):
            raise HTTPException(404, '자료를 찾지 못했습니다.')
        return {'deleted': True}

    @app.get('/api/sources/{chunk_id}')
    def source(chunk_id: str):
        result = app.state.store.chunk(chunk_id)
        if not result:
            raise HTTPException(404, '원문을 찾지 못했습니다.')
        return result

    @app.get('/api/history/{session}')
    def history(session: str, document_ids: str = ''):
        return app.state.store.history(session, document_ids.split(',') if document_ids else [])

    @app.get('/api/progress/{request_id}')
    def progress(request_id: str):
        return {'detail': app.state.progress.get(request_id, '')}

    @app.post('/api/chat')
    async def chat(req: ChatRequest):
        if not req.message.strip():
            raise HTTPException(400, '질문을 입력해 주세요.')
        ids = sorted(set(req.document_ids))
        known = {d['id'] for d in app.state.store.documents()}
        if not set(ids) <= known:
            raise HTTPException(404, '선택한 자료가 없습니다. 목록을 새로고침해 주세요.')
        key = (req.session_id, tuple(ids))
        # A lock exists only while the request is running; keep concurrent turns ordered.
        lock, count = app.state.locks.get(key, (asyncio.Lock(), 0))
        app.state.locks[key] = (lock, count + 1)
        app.state.progress[req.request_id] = '자료를 확인하는 중'
        try:
            async with lock:
                start = time.monotonic()
                store = app.state.store
                chunks = store.chunks(ids)
                try:
                    result = await app.state.graph.ainvoke({'message': req.message, 'mode': req.mode,
                        'chunks': chunks, 'progress_id': req.request_id, 'history': store.history(req.session_id, ids), 'events': []})
                except ModelError as exc:
                    raise HTTPException(503, str(exc)) from exc
                cited = set(CITATION.findall(result['answer']))
                used_sources = [c for c in result['sources'] if c['id'] in cited]
                stats = result.get('summary_data', {})
                metadata = {'summary_stats': {k: stats[k] for k in ('section_count','processed_chunks','map_calls','cache_hits') if k in stats}, 'mode': result['mode'], 'backend': app.state.model.backend,
                    'elapsed_ms': round((time.monotonic()-start)*1000),
                    'events': result['events'], 'coverage': result['coverage'],
                    'citation_check': result['valid'], 'attempts': result['attempts'],
                    'sources': used_sources, 'usage': result.get('usage', {})}
                store.save_turn(req.session_id, ids, req.message, result['answer'], metadata)
                return {'answer': result['answer'], 'session_id': req.session_id, **metadata}
        finally:
            app.state.progress.pop(req.request_id, None)
            _, count = app.state.locks[key]
            if count == 1:
                del app.state.locks[key]
            else:
                app.state.locks[key] = (lock, count - 1)

    return app


app = create_app()
