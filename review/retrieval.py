import math
import re
import unicodedata
from collections import Counter

STOP = {'정리', '요약', '설명', '해줘', '주세요', '알려줘', '무엇', '어떤', '수업', '자료', '그것', '내용', '대해', '뭐야', '차이', '쉽게'}


def terms(text):
    text = unicodedata.normalize('NFC', text).lower()
    words = re.findall(r'[a-z][a-z0-9_]*|[가-힣]+|[0-9]+', text)
    out = []
    for word in words:
        if word in STOP:
            continue
        out.append(word)
        # Korean particle variation is handled with character bigrams, without model downloads.
        if re.fullmatch('[가-힣]+', word) and len(word) > 2:
            out.extend(word[i:i+2] for i in range(len(word)-1))
    return out


def search(chunks, query, limit=6):
    query_terms = set(terms(query))
    bags = [Counter(terms(c['text'])) for c in chunks]
    n = len(bags)
    if not n or not query_terms:
        return []
    avg = sum(sum(b.values()) for b in bags) / n or 1
    frequency = {t: sum(t in b for b in bags) for t in query_terms}
    scored = []
    for c, bag in zip(chunks, bags):
        length = sum(bag.values())
        score = sum(math.log(1 + (n-frequency[t]+0.5)/(frequency[t]+0.5)) *
                    (bag[t]*2.2)/(bag[t]+1.2*(0.25+0.75*length/avg))
                    for t in query_terms if bag[t])
        if score > 0:
            scored.append(c | {'score': round(score, 4)})
    return sorted(scored, key=lambda c: c['score'], reverse=True)[:limit]


def overview(chunks, limit=12):
    # Round-robin documents with positions spread over each document.
    groups = {}
    for c in chunks:
        groups.setdefault(c['document_id'], []).append(c)
    queues = []
    for group in groups.values():
        count = min(len(group), limit)
        positions = [round(i * (len(group)-1)/max(1,count-1)) for i in range(count)]
        queues.append([group[p] for p in positions])
    selected = []
    for i in range(limit):
        for q in queues:
            if i < len(q) and len(selected) < limit:
                selected.append(q[i] | {'score': None})
    return selected
