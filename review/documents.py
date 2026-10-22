import io
import re
import unicodedata
from pathlib import Path
from pypdf import PdfReader

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 1_000_000


def parse_document(name, payload):
    name = unicodedata.normalize('NFC', Path(name.replace('\\', '/')).name)[:180]
    if len(payload) > MAX_BYTES:
        raise ValueError('파일은 10MB 이하로 등록해 주세요.')
    suffix = Path(name).suffix.lower()
    if suffix not in {'.txt', '.md', '.pdf'}:
        raise ValueError('TXT, MD, PDF 파일만 지원합니다.')
    if suffix == '.pdf':
        try:
            reader = PdfReader(io.BytesIO(payload))
            if reader.is_encrypted:
                raise ValueError('암호화된 PDF는 지원하지 않습니다.')
            if len(reader.pages) > 200:
                raise ValueError('PDF는 200페이지 이하로 나눠 등록해 주세요.')
            pages, chars = [], 0
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ''
                chars += len(text)
                if chars > MAX_CHARS:
                    raise ValueError('추출 텍스트가 너무 큽니다. 자료를 나눠 등록해 주세요.')
                pages.append((i + 1, text))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('PDF를 읽지 못했습니다. 손상 여부를 확인해 주세요.') from exc
    else:
        text = None
        for encoding in ('utf-8-sig', 'cp949'):
            try:
                text = payload.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        if text is None:
            raise ValueError('텍스트 인코딩을 읽지 못했습니다. UTF-8로 저장해 주세요.')
        pages = [(None, text)]
    if sum(len(t) for _, t in pages) > MAX_CHARS:
        raise ValueError('텍스트가 너무 큽니다. 자료를 나눠 등록해 주세요.')
    chunks = []
    for page, text in pages:
        lines = [unicodedata.normalize('NFC', re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)).strip() for s in text.splitlines()]
        current, start, end = [], 1, 1
        def flush():
            if current:
                chunks.append({'page': page, 'start_line': start, 'end_line': end, 'text': '\n'.join(current)})
        for line_no, line in enumerate(lines, 1):
            if not line:
                continue
            # Long transcript lines are split without losing the original line reference.
            for pos in range(0, len(line), 800):
                part = line[pos:pos + 800]
                if current and sum(map(len, current)) + len(part) > 900:
                    flush(); current = []
                if not current:
                    start = line_no
                current.append(part); end = line_no
        flush()
    if not chunks or sum(len(c['text'].strip()) for c in chunks) < 15:
        raise ValueError('읽을 수 있는 텍스트가 부족합니다. 스캔 PDF는 OCR 처리 후 등록해 주세요.')
    return name, chunks
