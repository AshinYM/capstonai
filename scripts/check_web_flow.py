"""Check running localhost app with existing sample; no cloud calls."""
import json
from pathlib import Path
import httpx
with httpx.Client(base_url='http://127.0.0.1:8600',timeout=180,trust_env=False) as client:
    health=client.get('/api/health');health.raise_for_status()
    assert health.json()['backend']=='ollama', 'This check requires local Ollama mode.'
    docs=client.get('/api/documents').json()
    sample=next((d for d in docs if d['name']=='LangGraph 복습 예시.txt'), None)
    if sample is None:
        sample=client.post('/api/documents/upload',files={'file':('수업예시.txt',Path('sample_data/수업예시.txt').read_bytes())}).json()
    response=client.post('/api/chat',json={'message':'정답과 해설을 포함해 복습 퀴즈를 만들어 줘','document_ids':[sample['id']],'session_id':'verification-local','mode':'quiz'})
    response.raise_for_status()
    result=response.json()
    report={'health':health.json(),'answer':result['answer'],'citation_check':result['citation_check'],'attempts':result['attempts'],'elapsed_ms':result['elapsed_ms']}
    print(json.dumps(report,ensure_ascii=False),flush=True)
    Path('data/web_flow_check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    assert result['citation_check'], 'Quiz fell back to source text.'
    assert '정답' in result['answer'] and '해설' in result['answer'], 'Model did not produce quiz answers/explanations.'
    assert all(client.get('/api/sources/'+s['id']).status_code==200 for s in result['sources'])
