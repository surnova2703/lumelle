"""Optional step 0: turn your own documents into training examples.

Put .docx / .pdf / .txt / .md files into training/my_docs/ and run (Ollama must be running):
    ../.venv/bin/python 0_import_docs.py

What it does:
  • tables with a question column and an answer column (like test answers) → question/answer pairs;
    rows marked «спорно» (disputed) are skipped, add --keep-disputed to keep them;
  • for every pair the teacher (qwen3:8b) writes a few other wordings of the question and a clear answer
    that sticks to YOUR answer, so the model learns the fact, not one sentence;
  • other text (notes, lectures) → the teacher writes questions about each fragment and answers them
    from the fragment only.
The result goes to training/data/docs.jsonl; step 1 (1_collect.py) adds it to the training set.

Note: fine-tuning teaches style better than facts. A small model will remember part of these facts and
may mix up the rest. To get exact answers from your files, use «Documents» in Lumelle instead.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from docs_search import extract_text, split_chunks  # noqa: E402
from main import MODEL, OLLAMA_URL, system_prompt  # noqa: E402

Q_WORDS = ("вопрос", "question", "задание", "task", "frage", "问题")
A_WORDS = ("ответ", "answer", "antwort", "réponse", "答案")
DISPUTED = re.compile(r"спорно|disputed|unsure", re.I)


def ask(model, messages, temperature, fmt=None):
    payload = {"model": model, "messages": messages, "stream": False, "think": False,
               "options": {"temperature": temperature}, "keep_alive": "30m"}
    if fmt:
        payload["format"] = fmt
    r = httpx.post(OLLAMA_URL, json=payload, timeout=900)
    r.raise_for_status()
    return re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S).strip()


def qa_pairs(text):
    """Rows «… | question | answer» of tables whose header names a question and an answer column."""
    pairs, notes = [], []
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        table = [l for l in lines if " | " in l]
        if len(table) >= 2:
            header = [h.strip().lower() for h in table[0].split(" | ")]
            qi = next((i for i, h in enumerate(header) if any(w in h for w in Q_WORDS)), None)
            ai = next((i for i, h in enumerate(header) if any(w in h for w in A_WORDS)), None)
            if qi is not None and ai is not None:
                for row in table[1:]:
                    cells = [c.strip() for c in row.split(" | ")]
                    if len(cells) > max(qi, ai) and cells[qi] and cells[ai]:
                        pairs.append((cells[qi], cells[ai]))
                continue
        notes.append(block.strip())
    return pairs, "\n\n".join(n for n in notes if n)


def lang_of(text):
    return "ru" if re.search("[а-яё]", text.lower()) else "en"


LIST = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}}, "required": ["items"]}
QA_LIST = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"q": {"type": "string"}, "a": {"type": "string"}}, "required": ["q", "a"]}}}, "required": ["items"]}


def from_pair(model, q, a, n):
    lang = "Russian" if lang_of(q + a) == "ru" else "English"
    try:
        alts = json.loads(ask(model, [{"role": "user", "content":
            f"A study question:\n{q}\n\nWrite {n} different ways a student could ask the same thing (full questions, "
            f"in {lang}). Return JSON {{\"items\": [...]}}"}], 0.8, LIST)).get("items", [])
    except (ValueError, httpx.HTTPError):
        alts = []
    questions = [q] + [x.strip() for x in alts if isinstance(x, str) and len(x.strip()) > 5][:n]
    system = system_prompt("chat", lang_of(q))
    out = []
    for question in questions:
        answer = ask(model, [{"role": "system", "content": system}, {"role": "user", "content":
            f"{question}\n\n(The correct answer from the course is: «{a}». Answer the question clearly in {lang}, "
            f"in 1–4 sentences, and keep exactly to this correct answer; do not add other options. "
            f"Do not mention that you were given the answer.)"}], 0.3)
        if answer:
            out.append({"mode": "chat", "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": question},
                                                     {"role": "assistant", "content": answer}]})
    return out


def from_notes(model, fragment, n):
    lang = "Russian" if lang_of(fragment) == "ru" else "English"
    try:
        items = json.loads(ask(model, [{"role": "user", "content":
            f"Study material:\n{fragment}\n\nWrite {n} questions a student could ask about it and short, accurate "
            f"answers using ONLY this material, in {lang}. Return JSON {{\"items\": [{{\"q\": ..., \"a\": ...}}]}}"}],
            0.4, QA_LIST)).get("items", [])
    except (ValueError, httpx.HTTPError):
        return []
    system = system_prompt("chat", lang_of(fragment))
    return [{"mode": "chat", "messages": [{"role": "system", "content": system}, {"role": "user", "content": it["q"]},
                                          {"role": "assistant", "content": it["a"]}]}
            for it in items if isinstance(it, dict) and it.get("q") and it.get("a")]


def main():
    ap = argparse.ArgumentParser(description="Turn your documents into training examples")
    ap.add_argument("--teacher", default=MODEL)
    ap.add_argument("--variants", type=int, default=3, help="other wordings per question (default 3)")
    ap.add_argument("--keep-disputed", action="store_true", help="keep rows marked «спорно»")
    args = ap.parse_args()

    src = HERE / "my_docs"
    src.mkdir(exist_ok=True)
    files = sorted(p for p in src.iterdir() if p.suffix.lower() in (".docx", ".pdf", ".txt", ".md"))
    if not files:
        print(f"Put your .docx / .pdf / .txt / .md files into {src} and run again.")
        return
    out_file = HERE / "data" / "docs.jsonl"
    out_file.parent.mkdir(exist_ok=True)
    examples = []
    for f in files:
        text = extract_text(f.name, f.read_bytes())
        pairs, notes = qa_pairs(text)
        skipped = [p for p in pairs if DISPUTED.search(p[1])] if not args.keep_disputed else []
        pairs = [p for p in pairs if p not in skipped]
        print(f"{f.name}: {len(pairs)} question–answer pairs" + (f" ({len(skipped)} disputed skipped)" if skipped else ""))
        for i, (q, a) in enumerate(pairs, 1):
            examples += from_pair(args.teacher, q, re.sub(r"\s*\((?:[^)]*спорно[^)]*)\)", "", a), args.variants)
            print(f"\r  pairs {i}/{len(pairs)}  examples: {len(examples)}", end="", flush=True)
        if pairs:
            print()
        if pairs and len(notes) < 1500:  # a file of test answers: the text around the tables is only headings
            notes = ""
        fragments = [c for c in split_chunks(notes) if len(c) > 200] if notes else []
        for i, frag in enumerate(fragments, 1):
            examples += from_notes(args.teacher, frag, 4)
            print(f"\r  text parts {i}/{len(fragments)}  examples: {len(examples)}", end="", flush=True)
        if fragments:
            print()
    out_file.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in examples), encoding="utf-8")
    print(f"Done: {len(examples)} examples → training/data/docs.jsonl")
    print("Next: ../.venv/bin/python 1_collect.py  (it adds these examples to the training set)")


if __name__ == "__main__":
    main()
