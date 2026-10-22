import re
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from .retrieval import search, overview
from .summary import FullSummary


class State(TypedDict, total=False):
    message: str
    mode: str
    chunks: list
    history: list
    sources: list
    answer: str
    events: list
    attempts: int
    valid: bool
    feedback: str
    usage: dict
    coverage: str
    summary_data: dict
    review_sources: list
    progress_id: str


def event(state, node, detail):
    return state.get('events', []) + [{'node': node, 'detail': detail}]


def excerpt_answer(state):
    sources = state['sources']
    if not sources:
        return '선택한 자료에서 질문과 관련된 근거를 찾지 못했어요. 수업 용어를 포함해 질문하거나 다른 자료를 선택해 주세요.'
    if state['mode'] == 'quiz':
        lines = ['복습 확인 질문 (원문 기반)', '']
        for i, c in enumerate(sources[:3], 1):
            line = next((s for s in c['text'].splitlines() if len(s) > 15), c['text'])[:220]
            lines += [f"{i}. 다음 내용의 의미를 본인 말로 설명해 보세요.", f"“{line}” [{c['id']}]", '']
        lines += ['확인 방법: 각 출처의 원문과 본인 설명을 비교하세요. 자동 채점은 제공하지 않습니다.']
    else:
        lines = ['핵심 발췌' if state['mode'] == 'summary' else '질문과 관련된 원문', '']
        for c in sources:
            text = c['text'][:550]
            lines += [f"[{c['id']}] {text}" + (' …' if len(c['text']) > 550 else ''), '']
    return '\n'.join(lines)


def citations_valid(answer, sources):
    allowed = {c['id'] for c in sources}
    cited = set(re.findall(r'\[([^\]\n]+)\]', answer))
    return bool(cited & allowed) and not (cited - allowed)


def build_graph(model, summary_cache=None, progress_callback=None):
    summarizer = FullSummary(model, summary_cache)
    def route(state):
        mode = state.get('mode', 'auto')
        if mode == 'auto':
            q = state['message']
            mode = 'quiz' if re.search('퀴즈|문제.*내|문제.*만들', q) else 'summary' if re.search('요약|전체.*정리|핵심.*정리', q) else 'ask'
        return {'mode': mode, 'attempts': 0, 'events': event(state, 'route', {'ask': '질문 답변', 'summary': '핵심 요약', 'quiz': '복습 질문'}[mode])}

    def retrieve(state):
        query = state['message']
        if state['mode'] == 'ask' and re.search('그거|그것|이거|그럼|다시|예시|더 쉽게', query):
            previous = [m['content'] for m in state['history'] if m['role'] == 'user']
            if previous:
                query += ' ' + previous[-1]
        broad = state['mode'] in {'summary', 'quiz'} or bool(re.search('실습.*주의|주의.*사항', query))
        full_summary = state['mode'] == 'summary' and model.backend != 'extractive'
        sources = state['chunks'] if full_summary else overview(state['chunks']) if broad else search(state['chunks'], query)
        if not sources and re.search('그거|그것|이거|그럼|다시|예시|더 쉽게', state['message']):
            prior = [m for m in state['history'] if m['role'] == 'assistant']
            if prior:
                previous_ids = {c['id'] for c in prior[-1].get('metadata', {}).get('sources', [])}
                sources = [c | {'score': None} for c in state['chunks'] if c['id'] in previous_ids][:6]
        coverage = f"전체 {len(state['chunks'])}개 구간 중 {len(sources)}개를 참고했습니다."
        if broad and len(sources) < len(state['chunks']):
            coverage += ' 긴 자료는 일부 구간을 고르게 뽑은 요약이므로 모든 내용을 포함하지 않습니다.'
        return {'sources': sources, 'coverage': coverage, 'events': event(state, 'retrieve', f'관련 구간 {len(sources)}개 찾음')}

    async def summarize_sections(state):
        callback = (lambda text: progress_callback(state.get('progress_id'), text)) if progress_callback else None
        data = await summarizer.prepare(state['sources'], callback)
        return {'summary_data': data,
                'coverage': f"전체 {len(state['chunks'])}개 구간을 {data['section_count']}개 부분으로 나눠 읽고 통합했습니다. 요약 과정에서 세부 내용은 생략될 수 있습니다.",
                'events': event(state, 'summarize_sections', f"전체 {data['processed_chunks']}개 구간 처리 · 부분 요약 {data['section_count']}개 · 캐시 {data['cache_hits']}개")}

    async def write(state):
        attempts = state['attempts'] + 1
        if model.backend == 'extractive' or not state['sources']:
            answer, usage = excerpt_answer(state), {}
        else:
            request = {'summary': '여러 부분의 요약을 통합해 핵심 개념을 4~7개 항목으로, 실습 주의점을 2~4개 항목으로 정리하세요. 서로 다른 구성 요소의 역할을 구분하고 원문에서 필수가 아닌 평가 도구를 의무로 단정하지 마세요. ', 'quiz': '정답·해설을 포함한 복습 퀴즈 3문항만 작성하세요. ', 'ask': '질문에만 3~6문장으로 답하세요. 퀴즈를 추가하지 마세요. '}[state['mode']] + state['message']
            data = state.get('summary_data')
            answer, usage = await model.generate(request, data['notes'] if data else state['sources'], [] if data else state['history'], state.get('feedback', ''), state['mode'])
            if data:
                answer = summarizer.restore_citations(answer, data['provenance'])
        return {'answer': answer, 'usage': usage, 'attempts': attempts, 'events': event(state, 'write', '답변 작성 완료')}

    async def review_summary(state):
        if progress_callback:
            progress_callback(state.get('progress_id'), '통합 요약을 원문과 대조하는 중')
        cited = set(re.findall(r'\[([^\]\n]+)\]', state['answer']))
        candidates = [c for c in state['sources'] if c['id'] in cited]
        ranked = search(candidates, state['answer'], limit=len(candidates)) or candidates
        evidence, size = [], 0
        for c in ranked:
            cost = len(c['text']) + 120
            if size + cost <= 4500:
                evidence.append(c); size += cost
        if not evidence:
            return {'events': event(state, 'review_summary', '대조할 출처 부족')}
        request = ('제공된 원문에서 직접 확인할 수 있는 내용만 새로 요약하세요. '
                   '핵심 개념 4~7개와 실습 주의점 2~4개를 줄바꿈한 번호 목록으로 작성하세요. '
                   '원문에 등장하는 구성 요소의 역할을 각각 구분하고, 도구·모델 이름은 교안의 예시로 표시하세요. '
                   '비교 예제는 그 예제의 조건을 함께 설명하세요. 검색 기술 전체의 정확도가 낮다고 단정하지 마세요. '
                   '실습 기법과 평가 도구는 교안에서 소개하는 선택지로 서술하세요. 특정 전략이나 평가 도구를 모든 실습의 필수 조건으로 쓰지 마세요. '
                   '각 항목에 실제로 사용한 원문 번호만 인용하세요.')
        answer, usage = await model.generate(request, evidence, [], mode='summary_review')
        return {'answer': answer, 'review_sources': evidence, 'usage': usage,
                'events': event(state, 'review_summary', f'관련 원문 {len(evidence)}개로 통합 요약 재검토')}

    def validate(state):
        valid = not state['sources'] or citations_valid(state['answer'], state.get('review_sources', state['sources']))
        quiz_ok = True
        if state['mode'] == 'quiz' and model.backend != 'extractive' and state['sources']:
            quiz_ok = '정답' in state['answer'] and '해설' in state['answer'] and all(
                re.search(str(i) + r'\s*(?:[.)번:]|\n)', state['answer']) for i in (1, 2, 3))
        valid = valid and quiz_ok
        feedback = '제공된 근거 번호를 [1] 또는 [2] 형식으로 반드시 포함하세요. 실제로 제공된 번호만 사용하세요.'
        if not quiz_ok:
            feedback += ' 요청은 퀴즈입니다. 1. 2. 3. 번호가 있는 질문 3개와 각 정답, 해설을 반드시 작성하세요.'
        return {'valid': valid, 'feedback': feedback,
                'events': event(state, 'validate', '출처·요청 형식 확인 완료' if valid else '출처 또는 요청 형식 수정 필요')}

    def finish(state):
        if not state['valid']:
            return {'answer': '모델 답변의 출처 또는 요청 형식을 확인하지 못해 원문을 대신 보여드립니다.\n\n' + excerpt_answer(state),
                    'events': event(state, 'finish', '출처 형식 검증 실패로 원문 반환')}
        return {'events': event(state, 'finish', '복습 완료')}

    graph = StateGraph(State)
    for name, node in [('route', route), ('retrieve', retrieve), ('summarize_sections', summarize_sections), ('write', write), ('review_summary', review_summary), ('validate', validate), ('finish', finish)]:
        graph.add_node(name, node)
    graph.add_edge(START, 'route'); graph.add_edge('route', 'retrieve')
    graph.add_conditional_edges('retrieve', lambda s: 'summarize_sections' if s['mode'] == 'summary' and model.backend != 'extractive' and s['sources'] else 'write', {'summarize_sections': 'summarize_sections', 'write': 'write'})
    graph.add_edge('summarize_sections', 'write')
    graph.add_conditional_edges('write', lambda s: 'review_summary' if s.get('summary_data') else 'validate', {'review_summary': 'review_summary', 'validate': 'validate'})
    graph.add_edge('review_summary', 'validate')
    graph.add_conditional_edges('validate', lambda s: 'write' if not s['valid'] and s['attempts'] < 2 else 'finish', {'write': 'write', 'finish': 'finish'})
    graph.add_edge('finish', END)
    return graph.compile()
