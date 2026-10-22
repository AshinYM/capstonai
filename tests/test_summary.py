import asyncio
import pytest
from fastapi.testclient import TestClient
from review.summary import FullSummary, SummaryCache, split_sections
from review.model import ModelError
from server import create_app


def chunks(n=60):
    return [{'id':f'chunk-{i}', 'document_id':'doc', 'name':'수업.pdf', 'page':i+1,
             'start_line':1, 'end_line':1, 'text':f'고유주제{i}: ' + '의미 검색과 저장소의 역할을 구분합니다. '*8} for i in range(n)]


class SummaryModel:
    backend='ollama'
    name='test-summary-model'
    def __init__(self):self.seen=[]
    async def generate(self, message, sources, history, feedback='', mode='ask'):
        self.seen.append([s['id'] for s in sources])
        if mode=='section_summary':
            return '\n'.join(f"{s['text'].split(':')[0]} [{s['id']}]" for s in sources), {}
        return '핵심 개념: ' + sources[0]['text'] + f" [{sources[0]['id']}]", {}


def test_all_chunks_are_mapped_and_cached(tmp_path):
    data=chunks()
    model=SummaryModel(); engine=FullSummary(model,SummaryCache(tmp_path/'cache.db'))
    progress=[]
    result=asyncio.run(engine.prepare(data,progress.append))
    assert result['processed_chunks']==60
    assert set(cid for call in model.seen for cid in call)=={c['id'] for c in data}
    assert result['section_count']>1 and progress[-1]=='부분별 핵심 개념을 통합하는 중'
    count=len(model.seen)
    repeated=asyncio.run(engine.prepare(data))
    assert repeated['map_calls']==0 and len(model.seen)==count
    assert repeated['cache_hits']==result['section_count']
    assert 'chunk-2' in [cid for ids in result['provenance'].values() for cid in ids]


def test_citation_provenance_is_restored():
    text=FullSummary.restore_citations('핵심 설명 [section-1]',{'section-1':['chunk-2','chunk-3']})
    assert text=='핵심 설명 [chunk-2] [chunk-3]'
    assert '[unknown]' in FullSummary.restore_citations('[unknown]',{})


def test_failed_sections_do_not_claim_success():
    class Bad(SummaryModel):
        async def generate(self,*args,**kw):return '근거 누락 [fake]',{}
    with pytest.raises(ModelError,match='전체 요약을 중단'):
        asyncio.run(FullSummary(Bad()).prepare(chunks(2)))


def test_sections_preserve_document_boundaries():
    data=chunks(4)
    data[2]['document_id']=data[3]['document_id']='doc-2'
    sections=split_sections(data)
    assert len(sections)==2
    assert sum(map(len,sections))==4


def test_api_reports_full_coverage_and_only_cited_sources(tmp_path):
    model=SummaryModel()
    with TestClient(create_app(tmp_path/'app.db',model)) as c:
        text='\n'.join(f'고유주제{i}: '+'수업의 서로 다른 개념입니다. '*22 for i in range(60))
        doc=c.post('/api/documents/text',json={'title':'긴 수업','text':text}).json()
        total=c.get('/api/documents').json()[0]['chunk_count']
        assert total>12
        result=c.post('/api/chat',json={'message':'핵심 요약','document_ids':[doc['id']],'mode':'summary','request_id':'coverage-test'}).json()
        assert result['summary_stats']['processed_chunks']==total
        assert '12개' not in result['coverage']
        assert result['citation_check']
        assert result['sources'] and all(f"[{s['id']}]" in result['answer'] for s in result['sources'])
        assert all(c.get('/api/sources/'+s['id']).status_code==200 for s in result['sources'])
        assert 'section-' not in result['answer']
        assert c.get('/api/progress/coverage-test').json()['detail']==''


def test_final_review_uses_original_sources(tmp_path):
    class Reviewer(SummaryModel):
        reviewed=False
        async def generate(self,message,sources,history,feedback='',mode='ask'):
            if mode=='summary_review':
                self.reviewed=True
                assert '임베딩 모델' not in message  # No forced Vector DB topic for other lessons.
                assert all(not c['id'].startswith(('section-','merged-')) for c in sources)
                return f"원문으로 다시 정리한 개념 [{sources[0]['id']}]", {}
            return await super().generate(message,sources,history,feedback,mode)
    model=Reviewer()
    with TestClient(create_app(tmp_path/'app.db',model)) as c:
        doc=c.post('/api/documents/text',json={'title':'교안','text':'서로 다른 구성 요소의 책임을 구분하는 수업 내용입니다.'}).json()
        result=c.post('/api/chat',json={'message':'요약','document_ids':[doc['id']],'mode':'summary'}).json()
        assert model.reviewed and result['citation_check']
        assert '원문으로 다시 정리' in result['answer']
        assert any(e['node']=='review_summary' for e in result['events'])


def test_modified_content_invalidates_section_cache(tmp_path):
    model=SummaryModel();engine=FullSummary(model,SummaryCache(tmp_path/'cache.db'))
    data=chunks(2)
    asyncio.run(engine.prepare(data));calls=len(model.seen)
    data[0]=dict(data[0],text='새로운 수업 내용과 수정된 정의입니다.')
    result=asyncio.run(engine.prepare(data))
    assert result['cache_hits']==0 and len(model.seen)>calls
