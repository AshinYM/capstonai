"""Paid OpenAI integration check. Reads .env locally; never prints credentials.

Uses only the included, authored LangGraph sample and an isolated temporary database.
Leaves the running app and .env backend unchanged. Makes real API calls when a key exists.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from fastapi.testclient import TestClient


def main():
    values = dotenv_values(ROOT / '.env')
    key = (values.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY') or '').strip()
    if not key:
        print('OPENAI_API_KEY가 비어 있습니다. 프로젝트 .env에 입력한 뒤 다시 실행하세요.')
        return 2
    os.environ['OPENAI_API_KEY'] = key
    os.environ['OPENAI_MODEL'] = values.get('OPENAI_MODEL') or 'gpt-4o-mini'
    os.environ['LLM_BACKEND'] = 'openai'
    from review.model import Model
    from server import create_app

    class ObservedModel(Model):
        outputs = []
        async def generate(self, *args, **kwargs):
            answer, usage = await super().generate(*args, **kwargs)
            self.outputs.append({'answer': answer, 'source_ids': [c['id'] for c in args[1]]})
            return answer, usage
    model = ObservedModel()
    print(f'실제 OpenAI API 검증 시작 · 모델 {model.name} · 호출 비용 발생', flush=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='review-openai-check-') as tmp:
        with TestClient(create_app(Path(tmp)/'test.db', model)) as client:
            sample = ROOT / 'sample_data' / '수업예시.txt'
            upload = client.post('/api/documents/upload', files={'file':(sample.name,sample.read_bytes())})
            upload.raise_for_status()
            doc = upload.json()
            for mode, message in [
                ('ask','일반 엣지와 조건부 엣지의 차이는 무엇인가요?'),
                ('quiz','수업 내용에 대한 정답과 해설을 포함한 퀴즈 3문항을 작성해 줘'),
                ('summary','수업 핵심 개념과 실습 주의점을 요약해 줘'),
            ]:
                if len(sys.argv)>1 and mode!=sys.argv[1]:
                    continue
                response = client.post('/api/chat', json={'message':message,'document_ids':[doc['id']],
                                        'session_id':'openai-check','mode':mode})
                if response.status_code != 200:
                    data = response.json()
                    print(f"{mode} 실패: HTTP {response.status_code} · {data.get('detail','오류')}", flush=True)
                    return 1
                data = response.json()
                if not data['citation_check'] or not data['sources']:
                    failure = ROOT/'data'/'openai_check_failure.json'
                    failure.parent.mkdir(exist_ok=True)
                    failure.write_text(json.dumps({'mode':mode,'result':data,'outputs':model.outputs},ensure_ascii=False,indent=2))
                    print(f'{mode} 실패: 모델 답변이 출처·요청 형식 검사를 통과하지 못했습니다. 상세: data/openai_check_failure.json', flush=True)
                    return 1
                assert all(f"[{s['id']}]" in data['answer'] for s in data['sources'])
                assert all(client.get('/api/sources/'+s['id']).status_code==200 for s in data['sources'])
                row = {'mode':mode,'answer':data['answer'],'elapsed_ms':data['elapsed_ms'],
                       'citation_check':data['citation_check'],'source_count':len(data['sources']),
                       'usage_last_generation':data.get('usage',{})}
                results.append(row)
                print(json.dumps({k:v for k,v in row.items() if k!='answer'},ensure_ascii=False),flush=True)
    output = ROOT/'data'/('openai_check.json' if len(sys.argv)==1 else f'openai_check_{sys.argv[1]}.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'model':model.name,'status':'passed','sample':'authored LangGraph sample',
                                 'note':'Format/source-ID checks; not an automatic factual-accuracy score.',
                                 'results':results},ensure_ascii=False,indent=2))
    print('검증 통과: ' + ', '.join(row['mode'] for row in results) + ' · 출처 조회. 결과: ' + str(output.relative_to(ROOT)),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
