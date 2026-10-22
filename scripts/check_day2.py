"""Final local-only smoke check of real lesson Q&A and quiz endpoints."""
import json
from pathlib import Path
import httpx

with httpx.Client(base_url='http://127.0.0.1:8600',timeout=240,trust_env=False) as client:
    health=client.get('/api/health');health.raise_for_status()
    assert health.json()['backend']=='ollama','Local Ollama mode required; no cloud calls.'
    docs=client.get('/api/documents').json()
    doc=next(d for d in docs if d['name'].endswith('코드).pdf'))
    results=[]
    for mode,message in [('ask','임베딩 모델과 벡터 DB는 각각 어떤 역할인가요?'),('quiz','선택한 수업 자료로 정답과 해설을 포함한 복습 퀴즈 3문항을 만들어 줘')]:
        r=client.post('/api/chat',json={'message':message,'document_ids':[doc['id']],'mode':mode,'session_id':'day2-final-check'})
        r.raise_for_status();data=r.json()
        assert data['citation_check'] and data['sources'],data['answer']
        assert all(f"[{s['id']}]" in data['answer'] for s in data['sources'])
        if mode=='quiz':assert data['answer'].count('정답:')==3 and data['answer'].count('해설:')==3
        row={'mode':mode,'answer':data['answer'],'source_count':len(data['sources']),'elapsed_ms':data['elapsed_ms'],'validation_passed':data['citation_check']}
        results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    report={'health':health.json(),'document':doc['name'],'results':results,'note':'Endpoint/format/source-ID checks; not a measured factual-accuracy score.'}
    Path('data/day2_final_check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
