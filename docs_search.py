"""Documents: text from .docx / .pdf / .xlsx / .txt / .md, split into fragments, offline keyword search (BM25).

No extra model is needed: words are reduced to a short stem, so «погрешность», «погрешности»
and «погрешностей» match each other. Works for Russian, English, French and German; Chinese is
searched by characters.
"""
import io
import math
import re
from collections import Counter
from typing import List, Tuple

MAX_CHUNK = 900      # characters in one fragment
OVERLAP = 150        # repeated between neighbouring fragments of plain text

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя
ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтобы без будто чего раз тоже себе под будет ж тогда кто
этот того потому этого какой совсем ним здесь этом один почти мой тем чтоб нее сейчас были куда зачем всех никогда можно при
наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою
этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это
какие каких каким какое какую бывают бывает такое такие сколько нужно почему объясни расскажи скажи назови перечисли
означает значит является являются называется называют чему чем какова каков каково
the a an and or of to in on at for with is are was were be been it this that these those as by from what which who how
does do explain tell name list mean means called kind kinds types type there
le la les un une des de du et est que qui dans pour sur der die das und ist ein eine zu mit von den im""".split())


# ---------- reading files ----------

def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".docx"):
        return _docx_text(data)
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if name.endswith((".xlsx", ".xlsm")):
        return _xlsx_text(data)
    if name.endswith((".txt", ".md", ".csv")):
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
    raise ValueError("unsupported")


def _docx_text(data: bytes) -> str:
    """Paragraphs and tables in document order; a table row becomes «cell | cell | cell»."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(io.BytesIO(data))
    out = []
    for el in d.element.body.iterchildren():
        tag = el.tag.split("}")[-1]
        if tag == "p":
            t = Paragraph(el, d).text.strip()
            if t:
                out.append(t)
        elif tag == "tbl":
            rows = []
            for row in Table(el, d).rows:
                cells = []
                for c in row.cells:
                    txt = " ".join(c.text.split())
                    if not cells or txt != cells[-1]:  # merged cells repeat their text
                        cells.append(txt)
                if any(cells):
                    rows.append(" | ".join(cells))
            out.append("\n".join(rows))
    return "\n\n".join(out)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}".replace(".", ",") if abs(v) < 1e15 else str(v)
    if hasattr(v, "strftime"):
        return v.strftime("%d.%m.%Y") if not getattr(v, "hour", 0) and not getattr(v, "minute", 0) else v.strftime("%d.%m.%Y %H:%M")
    return " ".join(str(v).split())


def _xlsx_text(data: bytes) -> str:
    """Every sheet becomes «Лист: name» + its rows as «cell | cell»; formulas give their last saved values."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        width = max(max(i for i, c in enumerate(r) if c) + 1 for r in rows)
        width = max(width, 2)  # so every row still looks like a table row
        out.append(f"Лист: {ws.title}")
        out.append("\n".join(" | ".join(r[:width] + [""] * (width - len(r[:width]))) for r in rows))
    wb.close()
    return "\n\n".join(out)


# ---------- fragments ----------

def split_chunks(text: str) -> List[str]:
    """Keeps paragraphs and table rows whole; a table's header row is repeated in every fragment."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks, cur, title = [], "", ""
    for b in blocks:
        lines = b.split("\n")
        if len(lines) > 1 and all(" | " in l for l in lines):  # a table
            header, rows = lines[0], lines[1:]
            if title:  # «Тест 2» above the table goes into every fragment of it
                header = title + "\n" + header
            part = header
            for r in rows:
                if len(part) + len(r) + 1 > MAX_CHUNK and part != header:
                    chunks.append(part)
                    part = header
                part += "\n" + r
            if title and cur.endswith(title):  # the heading already went into the table's fragments
                cur = cur[: -len(title)].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(part)
            continue
        title = b if len(lines) == 1 and len(b) <= 80 else ""
        if len(cur) + len(b) + 2 <= MAX_CHUNK:
            cur = (cur + "\n\n" + b).strip()
            continue
        if cur:
            chunks.append(cur)
        while len(b) > MAX_CHUNK:  # a very long paragraph: cut with an overlap
            cut = b.rfind(" ", 0, MAX_CHUNK)
            cut = cut if cut > MAX_CHUNK // 2 else MAX_CHUNK
            chunks.append(b[:cut])
            b = b[max(cut - OVERLAP, 0):]
        cur = b
    if cur:
        chunks.append(cur)
    return chunks


# ---------- search ----------

def terms(text: str) -> List[str]:
    out = []
    for w in re.findall(r"[\w]+", text.lower().replace("ё", "е")):
        if re.fullmatch(r"[一-鿿]+", w):  # Chinese: single characters and pairs
            out += list(w) + [w[i:i + 2] for i in range(len(w) - 1)]
            continue
        if w in STOP or len(w) < 2:
            continue
        out.append(w[:6] if len(w) > 6 else w)  # a rough stem: the first 6 letters
    return out


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


MIN_SIMILARITY = 0.45  # below this a fragment is not about the question


def hybrid(question: str, chunks: List[Tuple[int, str]], k: int = 4, qvec=None, vecs=None) -> List[Tuple[float, int, str]]:
    """Keyword search (BM25) plus search by meaning (embeddings), merged by rank.
    Without embeddings it is plain BM25. vecs: {chunk id: vector}."""
    by_words = search(question, chunks, k=k * 2)
    if not qvec or not vecs:
        return by_words[:k]
    texts = dict(chunks)
    sims = sorted(((cosine(qvec, v), cid) for cid, v in vecs.items() if cid in texts), reverse=True)
    by_meaning = [(s, cid) for s, cid in sims[:k * 2] if s >= MIN_SIMILARITY]
    if by_meaning:
        best = by_meaning[0][0]
        by_meaning = [(s, cid) for s, cid in by_meaning if s >= best - 0.12]  # keep only the close ones
    score = {}
    for rank, (_, cid, _) in enumerate(by_words):
        score[cid] = score.get(cid, 0) + 1 / (60 + rank)
    for rank, (_, cid) in enumerate(by_meaning):
        score[cid] = score.get(cid, 0) + 1 / (60 + rank)
    merged = sorted(score.items(), key=lambda x: -x[1])[:k]
    return [(s, cid, texts[cid]) for cid, s in merged]


def search(question: str, chunks: List[Tuple[int, str]], k: int = 4) -> List[Tuple[float, int, str]]:
    """BM25 over the user's fragments. chunks: [(id, text)] → [(score, id, text)], best first."""
    q = set(terms(question))
    if not q or not chunks:
        return []
    docs = [(cid, text, Counter(terms(text))) for cid, text in chunks]
    n = len(docs)
    avg = sum(sum(c.values()) for _, _, c in docs) / n or 1
    df = Counter(t for _, _, c in docs for t in set(c) if t in q)
    scored = []
    for cid, text, c in docs:
        length = sum(c.values()) or 1
        hits = sum(1 for t in q if c[t])
        if len(q) >= 2 and hits / len(q) < 0.4:  # one random common word is not a match
            continue
        s = 0.0
        for t in q:
            if c[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * c[t] * 2.2 / (c[t] + 1.2 * (0.25 + 0.75 * length / avg))
        if s > 0:
            scored.append((s, cid, text))
    scored.sort(reverse=True)
    if not scored:
        return []
    best = scored[0][0]
    return [x for x in scored[:k] if x[0] >= best * 0.35]  # drop weak matches
