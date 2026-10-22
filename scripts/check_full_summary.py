"""Local evaluation of full-document coverage; uses the configured local model only."""
import asyncio,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv()
from review.model import Model
from review.storage import Store
from review.summary import SummaryCache
from review.graph import build_graph,citations_valid

async def main():
    model=Model()
    if model.backend!='ollama':raise SystemExit('Local evaluation requires Ollama mode.')
    store=Store('data/review.db')
    doc=next(d for d in store.documents() if d['name'].endswith('코드).pdf'))
    sources=store.chunks([doc['id']])
    before=None
    with store.connect() as db:
        for row in db.execute("SELECT content,metadata,scope FROM messages WHERE role='assistant' ORDER BY id DESC"):
            meta=json.loads(row['metadata'])
            if json.loads(row['scope'])==[doc['id']] and meta.get('mode')=='summary' and not meta.get('summary_stats'):
                before={'answer':row['content'],'elapsed_ms':meta.get('elapsed_ms'),'source_count':len(meta.get('sources',[])),'coverage':meta.get('coverage')};break
    graph=build_graph(model,SummaryCache(store.path),lambda _,text:print(text,flush=True))
    start=time.monotonic()
    result=await graph.ainvoke({'message':'선택한 수업 자료의 핵심을 요약해 줘','mode':'summary','chunks':sources,'history':[],'events':[],'progress_id':'evaluation'})
    elapsed=round((time.monotonic()-start)*1000)
    stats=result['summary_data'];cited=[c for c in sources if '['+c['id']+']' in result['answer']]
    report={'document':doc['name'],'before':before,'after':{'answer':result['answer'],'coverage':result['coverage'],'elapsed_ms':elapsed,'cited_source_count':len(cited),'stats':{k:stats[k] for k in ('processed_chunks','section_count','map_calls','cache_hits')},'citation_check':result['valid']}}
    Path('data/summary_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    assert stats['processed_chunks']==len(sources)
    assert result['valid'] and citations_valid(result['answer'],sources)
    assert len(cited)>0

if __name__=='__main__':asyncio.run(main())
