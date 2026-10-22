"""Integration check against running local Ollama app, including progress polling."""
import asyncio,json,uuid
from pathlib import Path
import httpx

async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8600',timeout=300,trust_env=False) as client:
        health=(await client.get('/api/health')).json()
        assert health['backend']=='ollama','This test is local-model only.'
        docs=(await client.get('/api/documents')).json()
        doc=next(d for d in docs if d['name'].endswith('코드).pdf'))
        rid=uuid.uuid4().hex
        task=asyncio.create_task(client.post('/api/chat',json={'message':'수업의 핵심을 요약해 줘','document_ids':[doc['id']],'mode':'summary','request_id':rid,'session_id':'full-summary-verification'}))
        progress=[]
        while not task.done():
            await asyncio.sleep(2)
            p=(await client.get('/api/progress/'+rid)).json()['detail']
            if p and (not progress or progress[-1]!=p):progress.append(p);print(p,flush=True)
        response=await task;response.raise_for_status();result=response.json()
        assert result['summary_stats']['processed_chunks']==doc['chunk_count']
        assert result['citation_check'] and result['sources']
        assert all(f"[{c['id']}]" in result['answer'] for c in result['sources'])
        assert (await client.get('/api/progress/'+rid)).json()['detail']==''
        report={'status':'passed','summary_stats':result['summary_stats'],'source_count':len(result['sources']),'elapsed_ms':result['elapsed_ms'],'progress':progress}
        Path('data/summary_endpoint_check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)

asyncio.run(main())
