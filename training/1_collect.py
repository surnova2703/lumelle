"""Step 1: collect training examples. The current model (the "teacher", qwen3:8b by default)
answers tasks exactly the way Lumelle does, and the answers are saved for training.

Run with Lumelle's own Python, while Ollama is running (Lumelle itself may keep running):
    ../.venv/bin/python 1_collect.py                 # tasks from prompts.txt + 3 similar ones for each
    ../.venv/bin/python 1_collect.py --expand 0      # only the tasks from prompts.txt
    ../.venv/bin/python 1_collect.py --from-chats    # also the questions from your own chats (admin)

The script can be stopped (Ctrl+C) and started again: finished examples are kept.
"""
import argparse
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from main import MODEL, OLLAMA_URL, TEMPERATURE, system_prompt  # noqa: E402  (Lumelle's own prompts)

MODES = ("chat", "write", "translate", "learn")  # Slides answers with JSON for a file, it is left out


def ask(model, messages, temperature, fmt=None):
    payload = {"model": model, "messages": messages, "stream": False, "think": False,
               "options": {"temperature": temperature}, "keep_alive": "30m"}
    if fmt:
        payload["format"] = fmt
    r = httpx.post(OLLAMA_URL, json=payload, timeout=900)
    r.raise_for_status()
    text = r.json()["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def lang_of(text):
    return "ru" if re.search("[а-яё]", text.lower()) else "en"


def read_seeds(path):
    seeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or "\t" not in line:
            continue
        mode, prompt = line.split("\t", 1)
        if mode in MODES:
            seeds.append((mode, prompt.strip()))
    return seeds


def chat_questions(db_path):
    """The first question of every admin chat (text modes only)."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT c.mode, m.content FROM chats c JOIN messages m ON m.chat_id = c.id "
        "WHERE c.owner = 'admin' AND m.role = 'user' AND m.id = "
        "(SELECT MIN(id) FROM messages WHERE chat_id = c.id AND role = 'user')").fetchall()
    conn.close()
    return [(mode, text.strip()) for mode, text in rows if mode in MODES and text and len(text) < 2000]


def expand(model, mode, prompt, n):
    """Ask the teacher for n new tasks of the same kind, so the set is bigger and more varied."""
    schema = {"type": "object", "properties": {"tasks": {"type": "array", "items": {"type": "string"}}}, "required": ["tasks"]}
    lang = "Russian" if lang_of(prompt) == "ru" else "English"
    msg = (f"Here is a request a user sent to an AI assistant in '{mode}' mode:\n{prompt}\n\n"
           f"Write {n} NEW, different requests of the same kind that real users might send. "
           f"Vary the topic and wording. Write them in {lang}. Return JSON: {{\"tasks\": [...]}}")
    try:
        data = json.loads(ask(model, [{"role": "user", "content": msg}], 0.9, fmt=schema))
        return [t.strip() for t in data.get("tasks", []) if isinstance(t, str) and 5 < len(t.strip()) < 600][:n]
    except (ValueError, httpx.HTTPError):
        return []


def main():
    ap = argparse.ArgumentParser(description="Collect training examples from the teacher model")
    ap.add_argument("--teacher", default=MODEL, help=f"model that writes the answers (default {MODEL})")
    ap.add_argument("--expand", type=int, default=3, help="new similar tasks per task from prompts.txt (0 = none)")
    ap.add_argument("--from-chats", action="store_true", help="also use the questions from your own chats")
    ap.add_argument("--valid", type=float, default=0.1, help="share of examples kept aside for checking")
    args = ap.parse_args()

    data_dir = HERE / "data"
    data_dir.mkdir(exist_ok=True)
    tasks_file = data_dir / "tasks.jsonl"     # every task, made once
    answers_file = data_dir / "all.jsonl"     # task + teacher's answer

    # 1) the list of tasks
    if tasks_file.exists():
        tasks = [json.loads(l) for l in tasks_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"Tasks: {len(tasks)} (from {tasks_file.name})")
    else:
        seeds = read_seeds(HERE / "prompts.txt")
        tasks = [{"mode": m, "prompt": p} for m, p in seeds]
        if args.from_chats:
            mine = chat_questions(ROOT / "lumelle.db")
            print(f"Questions from your chats: {len(mine)}")
            tasks += [{"mode": m, "prompt": p} for m, p in mine]
        if args.expand > 0:
            print(f"Making {args.expand} similar tasks for each of {len(seeds)} seed tasks…")
            for i, (m, p) in enumerate(seeds, 1):
                tasks += [{"mode": m, "prompt": t} for t in expand(args.teacher, m, p, args.expand)]
                print(f"\r  {i}/{len(seeds)}  tasks: {len(tasks)}", end="", flush=True)
            print()
        seen, unique = set(), []
        for t in tasks:
            key = (t["mode"], t["prompt"].lower())
            if key not in seen:
                seen.add(key)
                unique.append(t)
        tasks = unique
        tasks_file.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks), encoding="utf-8")
        print(f"Tasks: {len(tasks)} → {tasks_file.name}")

    # 2) the teacher answers (skipping what is already done)
    done = set()
    if answers_file.exists():
        for l in answers_file.read_text(encoding="utf-8").splitlines():
            if l.strip():
                ex = json.loads(l)
                done.add((ex["mode"], ex["messages"][1]["content"]))
    todo = [t for t in tasks if (t["mode"], t["prompt"]) not in done]
    print(f"Answers: {len(done)} ready, {len(todo)} to go (teacher: {args.teacher})")
    with answers_file.open("a", encoding="utf-8") as out:
        for i, t in enumerate(todo, 1):
            system = system_prompt(t["mode"], lang_of(t["prompt"]))
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": t["prompt"]}]
            try:
                answer = ask(args.teacher, msgs, TEMPERATURE[t["mode"]])
            except httpx.HTTPError as e:
                print(f"\n  ! skipped (Ollama error: {e})")
                continue
            if answer:
                ex = {"mode": t["mode"], "messages": msgs + [{"role": "assistant", "content": answer}]}
                out.write(json.dumps(ex, ensure_ascii=False) + "\n")
                out.flush()
            print(f"\r  {i}/{len(todo)}", end="", flush=True)
    print()

    # 3) split into train / valid (the format MLX expects: {"messages": [...]})
    examples = [json.loads(l) for l in answers_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    random.Random(42).shuffle(examples)
    n_valid = max(1, int(len(examples) * args.valid)) if len(examples) > 10 else 1
    for name, part in (("valid", examples[:n_valid]), ("train", examples[n_valid:])):
        (data_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps({"messages": ex["messages"]}, ensure_ascii=False) + "\n" for ex in part), encoding="utf-8")
    print(f"Done: {len(examples) - n_valid} examples for training, {n_valid} for checking → training/data/")
    print("Next step: bash 2_train.sh")


if __name__ == "__main__":
    main()
