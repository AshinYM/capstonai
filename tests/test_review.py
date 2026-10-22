import io
import json
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from server import create_app
from review.documents import parse_document, MAX_BYTES
from review.retrieval import search
from review.model import Model, ModelError
from review.graph import citations_valid


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_BACKEND', 'extractive')
    with TestClient(create_app(tmp_path/'review.db')) as c:
        yield c


def add(client, title='LangGraph', text='LangGraph의 조건부 엣지는 상태에 따라 다음 노드를 선택합니다. 종료 조건을 설정해야 합니다.'):
    response = client.post('/api/documents/text', json={'title': title, 'text': text})
    assert response.status_code == 200, response.text
    return response.json()['id']


def chat(client, ids, message='조건부 엣지는 무엇인가요?', **kw):
    return client.post('/api/chat', json={'message': message, 'document_ids': ids, 'session_id': 'test', **kw})


def test_grounded_answer_and_source(client):
    doc = add(client)
    r = chat(client, [doc]); assert r.status_code == 200
    result = r.json()
    assert '상태에 따라' in result['answer']
    assert result['citation_check']
    cid = result['sources'][0]['id']
    assert f'[{cid}]' in result['answer']
    assert client.get('/api/sources/'+cid).json()['start_line'] == 1
    assert [e['node'] for e in result['events']] == ['route','retrieve','write','validate','finish']


def test_irrelevant_question_abstains(client):
    doc = add(client)
    result = chat(client,[doc], '우주왕복선 연료탱크 가격은?').json()
    assert result['sources'] == []
    assert '찾지 못했어요' in result['answer']


def test_scope_excludes_other_documents(client):
    one = add(client)
    two = add(client,'다른 수업','우주왕복선의 연료탱크 가격은 수업 범위에 포함되지 않습니다.')
    result = chat(client,[one], '우주왕복선 연료탱크 가격은?').json()
    assert not result['sources']
    assert all(c['document_id'] == two for c in chat(client,[two], '연료탱크').json()['sources'])


def test_dedup_delete_and_stale_selection(client):
    one = add(client)
    assert add(client) == one
    assert len(client.get('/api/documents').json()) == 1
    assert client.delete('/api/documents/'+one).status_code == 200
    assert chat(client,[one]).status_code == 404
    assert client.delete('/api/documents/'+one).status_code == 404


def test_text_validation_and_upload(client):
    assert client.post('/api/documents/upload',files={'file':('x.exe',b'x'*100)}).status_code == 400
    assert client.post('/api/documents/upload',files={'file':('x.txt',b' ')}).status_code == 400
    payload = '벡터 검색은 의미가 가까운 문서를 찾는 방식입니다.'.encode('cp949')
    r = client.post('/api/documents/upload',files={'file':('../수업.txt',payload)})
    assert r.status_code == 200
    assert r.json()['name'] == '수업.txt'
    assert client.post('/api/chat',json={'message':' ','document_ids':[r.json()['id']]}).status_code == 400
    assert client.post('/api/chat',json={'message':'질문','document_ids':[]}).status_code == 422


def test_scanned_pdf_and_limits():
    writer = PdfWriter(); writer.add_blank_page(width=200,height=200)
    stream = io.BytesIO(); writer.write(stream)
    with pytest.raises(ValueError, match='OCR'): parse_document('scan.pdf',stream.getvalue())
    with pytest.raises(ValueError, match='10MB'): parse_document('large.txt',b'a'*(MAX_BYTES+1))
    with pytest.raises(ValueError): parse_document('broken.pdf',b'%PDF invalid')


def test_summary_quiz_and_history_scope(client):
    one = add(client)
    result = chat(client,[one],'핵심 요약',mode='summary').json()
    assert result['mode'] == 'summary' and '핵심 발췌' in result['answer']
    quiz = chat(client,[one],'퀴즈',mode='quiz').json()
    assert '본인 말로' in quiz['answer']
    rows = client.get('/api/history/test',params={'document_ids':one}).json()
    assert len(rows) == 4
    assert client.get('/api/history/test',params={'document_ids':'other'}).json() == []


def test_persistence_and_long_line(tmp_path,monkeypatch):
    monkeypatch.setenv('LLM_BACKEND','extractive')
    db = tmp_path/'persist.db'
    with TestClient(create_app(db)) as c:
        one=add(c); chat(c,[one])
    with TestClient(create_app(db)) as c:
        assert len(c.get('/api/documents').json())==1
        assert len(c.get('/api/history/test',params={'document_ids':one}).json())==2
    _, chunks=parse_document('long.txt',('조건부엣지'*1000).encode())
    assert len(chunks)>1 and all(len(c['text'])<=901 for c in chunks)


class BadCitationModel:
    backend='ollama'
    name='test-double'
    async def generate(self,*args):
        return '잘못된 인용입니다. [invented-source]', {}


def test_invalid_citation_retries_then_extracts(tmp_path):
    with TestClient(create_app(tmp_path/'test.db',BadCitationModel())) as c:
        one=add(c); r=chat(c,[one]).json()
        assert r['attempts']==2 and not r['citation_check']
        assert '원문을 대신' in r['answer']
        assert 'invented-source' not in r['answer']


class FailingModel(BadCitationModel):
    async def generate(self,*args): raise ModelError('모델 서버 실행을 확인해 주세요.')


def test_model_failure_is_not_fake_success(tmp_path):
    with TestClient(create_app(tmp_path/'test.db',FailingModel())) as c:
        one=add(c); r=chat(c,[one])
        assert r.status_code==503
        assert not c.get('/api/history/test',params={'document_ids':one}).json()


def test_citation_check_rejects_unknown():
    assert citations_valid('설명 [abc-1]',[{'id':'abc-1'}])
    assert not citations_valid('설명 [unknown]',[{'id':'abc-1'}])
    assert not citations_valid('출처 없음',[{'id':'abc-1'}])


def test_health_and_static(client):
    assert client.get('/api/health').json()['backend']=='extractive'
    assert '다시봄' in client.get('/').text
    assert client.get('/static/app.js').status_code==200


def test_real_pdf_text_can_be_read(client):
    # Hand-written PDF stream avoids adding a document-generation dependency.
    from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
    w = PdfWriter(); page = w.add_blank_page(width=400, height=300)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'):w._add_object(font)})})
    stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 10 200 Td (LangGraph conditional routing selects a node.) Tj ET')
    page[NameObject('/Contents')] = w._add_object(stream)
    buf=io.BytesIO();w.write(buf)
    r=client.post('/api/documents/upload', files={'file':('lecture.pdf',buf.getvalue())})
    assert r.status_code==200
    result=chat(client,[r.json()['id']], 'LangGraph routing').json()
    assert result['sources'][0]['page']==1
    assert 'conditional routing' in result['answer']


@pytest.mark.parametrize('backend', ['openai','ollama'])
@pytest.mark.parametrize('citation', ['abc-1', '1'])
def test_model_adapter_parses_mocked_response(monkeypatch,backend,citation):
    import asyncio
    import httpx
    from review import model as module
    monkeypatch.setenv('LLM_BACKEND', backend)
    monkeypatch.setenv('OPENAI_API_KEY','test-key-not-real')
    original=httpx.AsyncClient
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        body = {'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'answer':f'설명 [{citation}]','source_numbers':[1]})}]}]} if backend=='openai' else {'message':{'content':json.dumps({'answer':f'설명 [{citation}]','source_numbers':[1]})}}
        return httpx.Response(200,json=body)
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    sources=[{'id':'abc-1','name':'수업.txt','page':None,'start_line':1,'end_line':1,'text':'설명 근거'}]
    answer,_=asyncio.run(Model().generate('질문',sources,[]))
    assert answer.startswith('설명 [abc-1]')
    assert '[1]' not in answer
    assert requests[0]['store'] is False if backend=='openai' else requests[0]['stream'] is False


def test_followup_and_practice_warnings_keep_context(client):
    one=add(client,'반복 수업','반복 그래프는 종료 조건과 반복 횟수 제한을 함께 두어 무한 반복을 막아야 합니다.')
    first=chat(client,[one],'핵심 요약',mode='summary').json()
    followup=chat(client,[one],'그거 좀 더 쉽게 설명해 줘',mode='ask').json()
    assert followup['sources'][0]['id']==first['sources'][0]['id']
    warnings=chat(client,[one],'실습할 때 주의할 점을 알려줘',mode='ask').json()
    assert warnings['sources'] and '반복 횟수' in warnings['answer']


class WrongTaskModel(BadCitationModel):
    async def generate(self,message,sources,*args):
        return f"요약만 작성했습니다. [{sources[0]['id']}]", {}


def test_valid_citation_does_not_hide_wrong_quiz_task(tmp_path):
    with TestClient(create_app(tmp_path/'test.db',WrongTaskModel())) as c:
        one=add(c); result=chat(c,[one],'퀴즈',mode='quiz').json()
        assert result['attempts']==2
        assert not result['citation_check']
        assert '요청 형식' in result['answer']


@pytest.mark.parametrize('backend',['ollama','openai'])
def test_structured_quiz_adapter(monkeypatch,backend):
    import asyncio
    import httpx
    from review import model as module
    monkeypatch.setenv('LLM_BACKEND',backend)
    monkeypatch.setenv('OPENAI_API_KEY','test-key-not-real')
    original=httpx.AsyncClient
    questions=[{'question':f'확인 질문 {i}?','answer':'정답 내용','explanation':'원문 근거 설명','source_numbers':[1]} for i in range(1,4)]
    def handler(request):
        sent=json.loads(request.content)
        schema = sent['format'] if backend=='ollama' else sent['text']['format']['schema']
        assert schema['properties']['questions']['minItems']==3
        content=json.dumps({'questions':questions})
        body={'message':{'content':content}} if backend=='ollama' else {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':content}]}]}
        return httpx.Response(200,json=body)
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    sources=[{'id':'abc-1','name':'수업.txt','page':None,'start_line':1,'end_line':1,'text':'설명 근거'}]
    answer,_=asyncio.run(Model().generate('퀴즈',sources,[],mode='quiz'))
    assert answer.count('정답:')==3 and answer.count('해설:')==3
    assert citations_valid(answer,sources)


@pytest.mark.parametrize('failure,expected', [('timeout','대기 시간'),('connection','서버에 연결'),('json','모델에는 연결'),('length','출력 길이 제한')])
def test_model_errors_distinguish_transport_and_format(monkeypatch,failure,expected):
    import asyncio
    import httpx
    from review import model as module
    monkeypatch.setenv('LLM_BACKEND','ollama')
    original=httpx.AsyncClient
    def handler(request):
        if failure=='timeout': raise httpx.ReadTimeout('test timeout',request=request)
        if failure=='connection': raise httpx.ConnectError('test unavailable',request=request)
        return httpx.Response(200,json={'done_reason':'length' if failure=='length' else 'stop','message':{'content':'{"answer":'}})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    sources=[{'id':'abc-1','name':'수업.txt','page':None,'start_line':1,'end_line':1,'text':'근거 내용'}]
    with pytest.raises(ModelError,match=expected):
        asyncio.run(Model().generate('설명해 줘',sources,[]))


def test_missing_openai_key_does_not_make_network_request(monkeypatch):
    import asyncio
    import httpx
    from review import model as module
    monkeypatch.setenv('LLM_BACKEND','openai')
    monkeypatch.setenv('OPENAI_API_KEY','')
    original=httpx.AsyncClient
    def handler(request):
        pytest.fail('No request should be sent without an API key.')
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    sources=[{'id':'abc-1','name':'sample.txt','page':None,'start_line':1,'end_line':1,'text':'sample evidence'}]
    with pytest.raises(ModelError,match='OPENAI_API_KEY가 없습니다'):
        asyncio.run(Model().generate('question',sources,[]))
