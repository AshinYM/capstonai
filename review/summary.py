"""Full-document map/reduce summaries with original-source provenance and reusable sections."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .model import ModelError

VERSION = 'full-summary-v1'
CITATION = re.compile(r'\[([^\]\n]+)\]')


def split_sections(chunks, max_chars=2800):
    sections, batch, length, doc = [], [], 0, None
    for chunk in chunks:
        cost = len(chunk['text']) + 100
        if batch and (length + cost > max_chars or doc != chunk['document_id']):
            sections.append(batch); batch, length = [], 0
        batch.append(chunk); length += cost; doc = chunk['document_id']
    if batch:
        sections.append(batch)
    return sections


class SummaryCache:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS section_summaries(key TEXT PRIMARY KEY, value TEXT)')

    def key(self, model, sources):
        payload = [VERSION, model.backend, model.name, [(c['id'], c['text']) for c in sources]]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def get(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT value FROM section_summaries WHERE key=?', (key,)).fetchone()
            return row[0] if row else None

    def put(self, key, answer):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO section_summaries VALUES(?,?)', (key, answer))


class FullSummary:
    def __init__(self, model, cache=None):
        self.model, self.cache = model, cache

    async def prepare(self, chunks, progress=None):
        sections = split_sections(chunks)
        notes, provenance, calls, cache_hits = [], {}, 0, 0
        for i, batch in enumerate(sections, 1):
            if progress:
                progress(f'자료 전체 읽는 중 · {i}/{len(sections)} 부분')
            key = self.cache.key(self.model, batch) if self.cache else None
            text = self.cache.get(key) if self.cache else None
            allowed = {c['id'] for c in batch}
            if text is not None:
                cache_hits += 1
            else:
                for attempt in range(2):
                    text, _ = await self.model.generate(
                        '이 부분에 등장하는 서로 다른 핵심 개념을 최대 5개로 정리하세요. '
                        '개념마다 담당 역할을 구분하고 예제 코드의 목적과 실습 유의점을 포함하세요. '
                        '평가 도구 소개를 필수 절차로 바꾸지 마세요. 자료에 없는 내용은 추가하지 마세요. '
                        '각 항목에 근거 번호를 인용하고 총 500자 이내로 작성하세요.',
                        batch, [], '제공된 근거 번호를 반드시 인용하세요.' if attempt else '', 'section_summary')
                    calls += 1
                    cited = set(CITATION.findall(text))
                    if cited & allowed and cited <= allowed:
                        break
                else:
                    raise ModelError(f'{i}번째 부분의 출처를 확인하지 못해 전체 요약을 중단했습니다. 다시 시도해 주세요.')
                if self.cache:
                    self.cache.put(key, text)
            cited = set(CITATION.findall(text))
            nid = f'section-{i}'
            provenance[nid] = [c['id'] for c in batch if c['id'] in cited]
            notes.append({'id': nid, 'document_id': batch[0]['document_id'],
                          'name': batch[0]['name'] + f' · 부분 {i}', 'page': None,
                          'start_line': 1, 'end_line': 1,
                          'text': CITATION.sub('', text).strip()})
        # Recursively reduce large collections; never truncate away unprocessed sections.
        rounds = 0
        while len(notes) > 8 or sum(len(n['text']) for n in notes) > 6000:
            reduced, expanded = [], {}
            for i, group in enumerate(split_sections([dict(n, document_id='combined') for n in notes], 3600), 1):
                text, _ = await self.model.generate(
                    '각 부분에서 서로 다른 주제를 유지하며 중복만 합쳐 600자 이내로 요약하세요. '
                    '사용한 모든 근거 번호를 인용하세요. 예시와 필수 조건을 구분하세요.',
                    group, [], mode='section_summary')
                calls += 1
                cited = set(CITATION.findall(text))
                allowed = {n['id'] for n in group}
                if not cited or not cited <= allowed:
                    raise ModelError('중간 요약의 출처를 확인하지 못했습니다. 다시 시도해 주세요.')
                nid = f'merged-{rounds}-{i}'
                expanded[nid] = list(dict.fromkeys(cid for n in group if n['id'] in cited for cid in provenance[n['id']]))
                reduced.append(dict(group[0], id=nid, document_id='combined', text=CITATION.sub('', text).strip()))
            if len(reduced) >= len(notes):
                raise ModelError('자료가 너무 길어 요약을 더 줄이지 못했습니다. 자료를 나누어 선택해 주세요.')
            notes, provenance = reduced, expanded
            rounds += 1
        if progress:
            progress('부분별 핵심 개념을 통합하는 중')
        return {'notes': notes, 'provenance': provenance, 'section_count': len(sections),
                'processed_chunks': len(chunks), 'map_calls': calls, 'cache_hits': cache_hits}

    @staticmethod
    def restore_citations(answer, provenance):
        return CITATION.sub(lambda m: ' '.join(f'[{cid}]' for cid in provenance[m.group(1)])
                            if m.group(1) in provenance else m.group(0), answer)
