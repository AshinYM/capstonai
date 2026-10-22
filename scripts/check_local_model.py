"""Optional real local-model check. No cloud API calls."""
import asyncio
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['LLM_BACKEND']='ollama'
os.environ.setdefault('OLLAMA_MODEL','qwen3:8b')
from review.model import Model
from review.documents import parse_document
from review.graph import build_graph

async def main():
    sample=Path('sample_data/수업예시.txt')
    name,chunks=parse_document(sample.name,sample.read_bytes())
    sources=[dict(c,id=f'sample-{i+1}',name=name,document_id='sample') for i,c in enumerate(chunks)]
    model=Model(); graph=build_graph(model)
    output=[]
    for mode,q in [('ask','일반 엣지와 조건부 엣지의 차이는 무엇인가요?'),('summary','핵심 개념과 실습 주의점을 요약해 줘')]:
        result=await graph.ainvoke({'message':q,'mode':mode,'chunks':sources,'history':[],'events':[]})
        record={'mode':mode,'answer':result['answer'],'citation_check':result['valid'],'attempts':result['attempts']}
        print(json.dumps(record,ensure_ascii=False),flush=True);output.append(record)
    Path('data/local_model_check.json').write_text(json.dumps({'model':model.name,'results':output},ensure_ascii=False,indent=2))
    if not all(x['citation_check'] for x in output):
        raise SystemExit('Local model did not produce valid citations.')

if __name__=='__main__':asyncio.run(main())
