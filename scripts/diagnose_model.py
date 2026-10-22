"""Print model transport/format diagnostics without document content or API keys."""
import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
import httpx
from review import model as module
from review.storage import Store
from review.retrieval import overview
original=httpx.AsyncClient
class DiagnosticClient(original):
    async def post(self,*a,**kw):
        prompt=kw['json'].get('messages',[])
        print('request_chars',sum(len(x['content']) for x in prompt),flush=True)
        try:r=await super().post(*a,**kw)
        except Exception as exc:
            print('transport_error',type(exc).__name__,flush=True);raise
        data=r.json();content=data.get('message',{}).get('content','')
        try:json.loads(content);valid=True
        except ValueError:valid=False
        print(json.dumps({k:data.get(k) for k in ['done_reason','prompt_eval_count','eval_count','total_duration']}), 'content_chars',len(content),'json_valid',valid,flush=True)
        return r
module.httpx.AsyncClient=DiagnosticClient
async def main():
    store=Store('data/review.db');ids=[d['id'] for d in store.documents()]
    sources=overview(store.chunks(ids))
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else 'summary'
        request = '정답과 해설을 포함한 복습 퀴즈 3문항을 만들어 줘.' if mode == 'quiz' else '핵심 개념과 실습 주의점 두 항목으로 간결하게 요약하세요.'
        answer,_=await module.Model().generate(request,sources,[],mode=mode)
        print('success_answer_chars',len(answer),flush=True)
    except Exception as exc:
        print('error',type(exc).__name__,'cause',type(exc.__cause__).__name__,flush=True)
asyncio.run(main())
