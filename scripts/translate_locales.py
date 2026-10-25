#!/usr/bin/env python3
"""Translate the interface's English strings with a local Ollama model.

The English files in frontend/src/locales/en/ are the source. For each
language asked for, this fills frontend/src/locales/<lang>/<area>.json with
every string that is missing there, or whose English changed since it was
translated. A string a person corrected by hand is never overwritten; when its
English changes it is reported instead.

    python3 scripts/translate_locales.py de es fr
    python3 scripts/translate_locales.py ja --area settings --model gemma4:12b
    python3 scripts/translate_locales.py --check          # report only, every language
    python3 scripts/translate_locales.py de --dry-run     # what would be translated

Nothing leaves the machine: the only network call is to the Ollama server
(OLLAMA_HOST, default http://localhost:11434). Each language folder also gets
_sources.json, which records the English each string was made from so the
next run knows what is stale. The app does not load that file.

Every translation is checked before it is written: the same {{placeholders}},
the same <tags>, the same $t() references, numbers and surrounding spaces as
the English, nothing that looks cut off or sits inside quotation marks the
English does not have, no single-brace {name} and no word that mixes Latin
and Cyrillic letters. One that fails twice is left out
and listed, and the interface shows the English for it. A machine translation
already on disk that fails a check added later is made again; one edited by
hand is only reported.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "src" / "locales"
SOURCES_FILE = "_sources.json"
PLURAL_SUFFIXES = ("zero", "one", "two", "few", "many", "other")

DEFAULT_MODEL = os.environ.get("GUAARDVARK_TRANSLATE_MODEL", "gemma4:12b")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA

# Names that stay as they are in every language.
KEEP = (
    "Guaardvark, Ollama, ComfyUI, Celery, Redis, PostgreSQL, Docker, GitHub, Hugging Face, "
    "LoRA, RAG, GPU, CPU, VRAM, API, MCP, URL, JSON, CSV, PDF, Wan, FLUX, Z-Image, Qwen, "
    "ACE-Step, Kokoro, Piper, Chatterbox, Whisper, Stable Audio, Film Crew, Interconnector, "
    "Uncle Claude, Swarm"
)

# How each language addresses the reader. A language not listed gets the
# register its modern software usually uses.
REGISTER = {
    "de": "Address the reader informally (du, dein), as modern German software does.",
    "es": "Address the reader informally (tú).",
    "fr": "Address the reader as vous.",
    "it": "Address the reader informally (tu).",
    "pt-BR": "Address the reader as você.",
    "nl": "Address the reader informally (je).",
    "ja": "Use polite, concise interface Japanese (です/ます where a sentence needs it).",
}

PLACEHOLDER = re.compile(r"\{\{\s*[^{}]+?\s*\}\}")
TAG = re.compile(r"</?[A-Za-z0-9]+\s*/?>")
NESTING = re.compile(r"\$t\([^)]*\)")

# English words a translation may write as a digit: Japanese, Korean and Chinese
# count with numerals, so "a minute" is 1分, "one per line" is 1行に1つ and
# "twice a day" is 1日2回.
NUMBER_WORDS = {
    "zero": "0", "none": "0", "no": "0", "a": "1", "an": "1", "one": "1", "once": "1", "single": "1", "solo": "1", "per": "1",
    "each": "1", "every": "1", "daily": "1", "nightly": "1", "hourly": "1",
    "two": "2", "twice": "2", "second": "2", "couple": "2", "both": "2", "pair": "2",
    "three": "3", "third": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10",
}
DIGIT_BY_PLACEHOLDER = re.compile(r"\d\s?\{\{|\}\}\s?\d")
# Opening and closing marks of the quotation styles a translation may use.
QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "„": "“", "«": "»", "‹": "›", "‘": "’", "「": "」", "『": "』"}
QUOTE_MARKS = set(QUOTE_PAIRS) | set(QUOTE_PAIRS.values())
# i18next fills {{name}}; a lone {name} is shown to the reader as written.
SINGLE_BRACE = re.compile(r"(?<!\{)\{[^{}]+\}(?!\})")
# Alphabets whose look-alike letters a model sometimes mixes inside one word ("Wznowь").
ALPHABETS = ("LATIN", "CYRILLIC", "GREEK")


# ---------------------------------------------------------------- files


def flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, path + "."))
        else:
            flat[path] = value
    return flat


def unflatten(flat: dict[str, str]) -> dict:
    tree: dict = {}
    for path, value in flat.items():
        node = tree
        *parents, leaf = path.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return tree


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def english_areas() -> dict[str, dict[str, str]]:
    return {
        p.stem: flatten(read_json(p))
        for p in sorted((LOCALES / "en").glob("*.json"))
        if not p.name.startswith("_")
    }


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- plurals


def plural_categories(language: str) -> dict[str, int]:
    """The plural categories a language uses, each with a count that selects it.

    Asks Node's Intl.PluralRules, the same rules i18next uses in the browser.
    """
    script = (
        "const r=new Intl.PluralRules(process.argv[1]);const o={};"
        "for(const c of r.resolvedOptions().pluralCategories){"
        "for(let n=0;n<1000;n++){if(r.select(n)===c){o[c]=n;break}}}"
        "console.log(JSON.stringify(o))"
    )
    node = shutil.which("node")
    if node:
        result = subprocess.run([node, "-e", script, language], capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
    print(f"  node not found; {language} plurals get one/other only", file=sys.stderr)
    return {"one": 1, "other": 2}


@functools.lru_cache(maxsize=None)
def one_means_one(language: str) -> bool:
    """True when the language's "one" form is used for the number 1 alone.

    Then a translation of that form may write the word for one instead of
    {{count}} ("Ein Bild ausgewählt"). Where "one" also covers 21 or 0, it may not.
    """
    script = (
        "const r=new Intl.PluralRules(process.argv[1]);let n=[];"
        "for(let i=0;i<1000;i++){if(r.select(i)==='one')n.push(i)}"
        "console.log(JSON.stringify(n.length===1&&n[0]===1&&r.select(1.5)!=='one'))"
    )
    node = shutil.which("node")
    if not node:
        return False
    result = subprocess.run([node, "-e", script, language], capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == "true"


def plural_family(key: str) -> tuple[str, str] | None:
    base, _, suffix = key.rpartition("_")
    return (base, suffix) if base and suffix in PLURAL_SUFFIXES else None


def target_keys(english: dict[str, str], categories: dict[str, int]) -> dict[str, tuple[str, int | None]]:
    """Every key the language needs, with the English it comes from.

    A plural family (x_one, x_other) becomes the language's own categories,
    each made from the English form that fits, or x_other.
    """
    families = {f[0] for k in english if (f := plural_family(k)) and f"{f[0]}_other" in english}
    wanted: dict[str, tuple[str, int | None]] = {}
    for key, text in english.items():
        family = plural_family(key)
        if family and family[0] in families:
            base, suffix = family
            if suffix == "zero":
                wanted[key] = (text, 0)
            if suffix == "other":
                for category, count in categories.items():
                    source = english.get(f"{base}_{category}", text)
                    wanted[f"{base}_{category}"] = (source, count)
            continue
        wanted[key] = (text, None)
    return wanted


# ---------------------------------------------------------------- checks


def numbers(text: str) -> list[str]:
    """The digits a person reads, outside placeholders, tags and $t() references."""
    for pattern in (PLACEHOLDER, TAG, NESTING):
        text = pattern.sub(" ", text)
    return sorted(re.findall(r"\d+", text))


def spelled_numbers(text: str) -> list[str]:
    """The digits the English writes as words, outside placeholders, tags and $t() references."""
    for pattern in (PLACEHOLDER, TAG, NESTING):
        text = pattern.sub(" ", text)
    return [NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in NUMBER_WORDS]


def only_spelled_added(source: str, translated: str, want: list[str], got: list[str]) -> bool:
    """Every English number is kept and each added one is a word the English
    spells out; a digit beside a placeholder ("1 {{count}}") is never one."""
    added = list(got)
    for digit in want:
        if digit not in added:
            return False
        added.remove(digit)
    spelled = spelled_numbers(source)
    for digit in added:
        if digit not in spelled:
            return False
        spelled.remove(digit)
    return not (DIGIT_BY_PLACEHOLDER.search(translated) and not DIGIT_BY_PLACEHOLDER.search(source))


def quoted_whole(text: str) -> bool:
    """The text is one quotation from end to end: 「ファイル生成」."""
    text = text.strip()
    return (len(text) > 2 and QUOTE_PAIRS.get(text[0]) == text[-1]
            and not any(c in QUOTE_MARKS for c in text[1:-1]))


def mixed_alphabet_words(text: str) -> list[str]:
    """Words that switch between Latin, Cyrillic and Greek letters partway through."""
    mixed = []
    for word in re.findall(r"[^\W\d_]+", text):
        alphabets = {unicodedata.name(c, "").split(" ")[0] for c in word}
        if len(alphabets & set(ALPHABETS)) > 1:
            mixed.append(word)
    return mixed


def width(text: str) -> int:
    """Columns the text takes: a Chinese, Japanese or Korean character is as
    wide as two Latin ones, so a complete sentence in those scripts is not
    mistaken for a cut-off one by its character count."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def problems(source: str, translated: str, may_drop_count: bool = False) -> list[str]:
    found = []
    if not isinstance(translated, str) or not translated.strip():
        return ["empty"]
    dropped_count = False
    for name, pattern in (("placeholders", PLACEHOLDER), ("tags", TAG), ("$t() references", NESTING)):
        want = sorted(m.replace(" ", "") for m in pattern.findall(source))
        got = sorted(m.replace(" ", "") for m in pattern.findall(translated))
        if may_drop_count and "{{count}}" in want and "{{count}}" not in got:
            want.remove("{{count}}")
            dropped_count = True
        if want != got:
            found.append(f"{name} {want} became {got}")
    # A singular that drops {{count}} may write the 1 out; any other number is
    # one the English does not show ("1 {{count}} voz" reads "1 1 voz").
    want, got = numbers(source), numbers(translated)
    if dropped_count and "1" in got:
        got.remove("1")
    if want != got and not only_spelled_added(source, translated, want, got):
        found.append(f"numbers {want} became {got}")
    if source.count("\n") != translated.count("\n"):
        found.append("line breaks changed")
    # A straight quote inside the answer can end the JSON string early, so the
    # translation stops mid-sentence and still parses.
    if source.count('"') % 2 == 0 and translated.count('"') % 2:
        found.append('unbalanced " (cut off at a quote mark?)')
    if quoted_whole(translated) and not quoted_whole(source):
        found.append("wrapped in quotation marks the English does not have")
    braces = sorted(SINGLE_BRACE.findall(translated))
    if braces != sorted(SINGLE_BRACE.findall(source)):
        found.append(f"single-brace {braces} (i18next shows it as written; placeholders are {{{{name}}}})")
    mixed = [w for w in mixed_alphabet_words(translated) if w not in mixed_alphabet_words(source)]
    if mixed:
        found.append(f"letters of two alphabets in one word: {mixed}")
    # Against GNOME's human translations of sentences of 40+ characters, 0.2-0.6%
    # of it/pt-BR/nl/pl/ru fall under 0.35, and by width 1.8% of zh-CN and 0.5%
    # of ja/ko (by character count it was half of zh-CN).
    if len(source) >= 40 and width(translated.strip()) < 0.35 * width(source):
        found.append("much shorter than the English (cut off?)")
    return found


def keep_edges(source: str, translated: str) -> str:
    """Give the translation the English string's leading and trailing spaces."""
    lead = source[: len(source) - len(source.lstrip())]
    trail = source[len(source.rstrip()):]
    return lead + translated.strip() + trail


# ---------------------------------------------------------------- model


def language_label(code: str) -> str:
    node = shutil.which("node")
    if node:
        result = subprocess.run(
            [node, "-e", "console.log(new Intl.DisplayNames(['en'],{type:'language'}).of(process.argv[1]))", code],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return code


def installed_models() -> list[str]:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as resp:
        return [m["name"] for m in json.load(resp).get("models", [])]


def ask_model(model: str, code: str, label: str, batch: dict[str, tuple[str, int | None]],
              feedback: str = "", glossary: dict[str, str] | None = None) -> dict:
    lines = []
    for key, (text, count) in batch.items():
        note = f" (plural form used when count = {count})" if count is not None else ""
        lines.append(f"{json.dumps(key)}: {json.dumps(text, ensure_ascii=False)}{note}")
    system = (
        f"You translate the interface of Guaardvark, a desktop AI studio that runs on the user's "
        f"own computer (chat, documents, images, video, music, code), from English into {label} "
        f"({code}). Each string is interface text: keep buttons and labels as short as the English, "
        f"keep sentences natural. {REGISTER.get(code, REGISTER.get(code.split('-')[0], 'Use the register modern software in this language uses.'))} "
        f"Keep these names as they are: {KEEP}. Keep every {{{{placeholder}}}}, <tag> and $t(...) "
        f"exactly as written, untranslated, and move it to where the {label} sentence needs it. "
        f"Every other word is translated, labels and headings included. Quote with {label} "
        f"quotation marks, never straight double quotes. The key before each string says where it appears. Answer with a JSON object mapping each "
        f"key to its translation, nothing else."
    )
    prompt = "Translate these strings:\n" + "\n".join(lines)
    if glossary:
        terms = "\n".join(f"{json.dumps(en, ensure_ascii=False)} = {json.dumps(tr, ensure_ascii=False)}" for en, tr in glossary.items())
        prompt = f"Use these established {label} terms wherever the English uses them:\n{terms}\n\n" + prompt
    if feedback:
        prompt += "\n\nYour last answer for some keys was rejected:\n" + feedback
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in batch},
            "required": list(batch),
        },
        "options": {"temperature": 0.2},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as resp:
        content = json.load(resp)["message"]["content"]
    try:
        answer = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return answer if isinstance(answer, dict) else {}


def batches(items: dict[str, tuple[str, int | None]], budget: int = 1500):
    batch: dict[str, tuple[str, int | None]] = {}
    size = 0
    for key, value in items.items():
        cost = len(key) + len(value[0]) + 8
        if batch and size + cost > budget:
            yield batch
            batch, size = {}, 0
        batch[key] = value
        size += cost
    if batch:
        yield batch


def translate_batch(model: str, code: str, label: str, batch: dict,
                    glossary: dict[str, str] | None = None) -> tuple[dict[str, str], dict[str, list[str]]]:
    done: dict[str, str] = {}
    failed: dict[str, list[str]] = {}
    pending = dict(batch)
    for attempt in range(2):
        feedback = "\n".join(f"{json.dumps(k)}: {'; '.join(v)}" for k, v in failed.items()) if attempt else ""
        answer = ask_model(model, code, label, pending, feedback, glossary)
        failed = {}
        for key, (source, _count) in pending.items():
            issues = problems(source, answer.get(key, ""), _count == 1 and key.endswith("_one") and one_means_one(code))
            if issues:
                failed[key] = issues
            else:
                done[key] = keep_edges(source, answer[key])
        pending = {k: v for k, v in pending.items() if k in failed}
        if not pending:
            break
    return done, failed


# ---------------------------------------------------------------- run


# Areas whose short labels become the glossary for the rest, so "Library" or
# "Settings" reads the same on every screen. They are translated first.
GLOSSARY_AREAS = ("navigation", "common")


def glossary_for(code: str, english: dict[str, dict[str, str]], limit: int = 80) -> dict[str, str]:
    terms: dict[str, str] = {}
    for area in GLOSSARY_AREAS:
        current = flatten(read_json(LOCALES / code / f"{area}.json"))
        for key, text in english.get(area, {}).items():
            have = current.get(key)
            if have and text != have and len(text.split()) <= 3 and not PLACEHOLDER.search(text):
                terms.setdefault(text.strip(), have.strip())
    return dict(list(terms.items())[:limit])


def terms_in(terms: dict[str, str], batch: dict[str, tuple[str, int | None]]) -> dict[str, str]:
    """The glossary entries whose English appears word for word in the batch.

    Given the whole list, the model also applies a short label to longer
    phrases: "Image Gen" became the title for "Image generation".
    """
    text = "\n".join(source for source, _count in batch.values())
    return {en: tr for en, tr in terms.items()
            if re.search(rf"(?<!\w){re.escape(en)}(?!\w)", text, re.IGNORECASE)}


def plan_language(code: str, english: dict[str, dict[str, str]], areas: list[str] | None):
    """What each area of one language needs: missing, stale, edited, extra."""
    folder = LOCALES / code
    sources = read_json(folder / SOURCES_FILE)
    categories = plural_categories(code)
    report = {}
    ordered = [a for a in GLOSSARY_AREAS if a in english] + [a for a in english if a not in GLOSSARY_AREAS]
    for area in ordered:
        strings = english[area]
        if areas and area not in areas:
            continue
        wanted = target_keys(strings, categories)
        current = flatten(read_json(folder / f"{area}.json"))
        record = sources.get(area, {})
        todo, edited_stale, invalid, adopted = {}, [], [], {}
        for key, (text, count) in wanted.items():
            have = current.get(key)
            seen = record.get(key)
            if have is None:
                todo[key] = (text, count)
                continue
            if problems(text, have, count == 1 and key.endswith("_one") and one_means_one(code)):
                if seen is not None and seen.get("out") == digest(have):
                    # Machine output that a newer check rejects: make it again.
                    todo[key] = (text, count)
                    continue
                invalid.append(key)
            if seen is None:
                # Written by hand, without a record: taken as made from today's English.
                adopted[key] = {"en": digest(text)}
            elif seen.get("en") == digest(text):
                continue
            elif seen.get("out") != digest(have):
                edited_stale.append(key)
            else:
                todo[key] = (text, count)
        extra = [k for k in current if k not in wanted]
        report[area] = {
            "wanted": wanted, "current": current, "record": record,
            "todo": todo, "edited_stale": edited_stale, "invalid": invalid, "extra": extra,
            "adopted": adopted,
        }
    return report, sources


def run_language(code: str, english, areas, model: str | None, dry_run: bool) -> tuple[int, int]:
    """Returns (strings left in English, hand-edited strings that fail the checks)."""
    report, sources = plan_language(code, english, areas)
    label = language_label(code)
    left_out = 0
    invalid = sum(len(plan["invalid"]) for plan in report.values())
    for area, plan in report.items():
        todo = plan["todo"]
        summary = f"{code}/{area}: {len(plan['wanted'])} strings, {len(todo)} to translate"
        if plan["extra"]:
            summary += f", {len(plan['extra'])} no longer in English"
        print(summary)
        for key in plan["edited_stale"]:
            print(f"  edited by hand, English has changed since: {area}.{key}")
        for key in plan["invalid"]:
            print(f"  edited by hand, fails the checks against the English: {area}.{key}")
        if dry_run or model is None:
            continue
        current, record = plan["current"], {**plan["record"], **plan["adopted"]}
        for key in plan["extra"]:
            current.pop(key, None)
            record.pop(key, None)
        terms = {} if area in GLOSSARY_AREAS else glossary_for(code, english)
        for batch in batches(todo):
            done, failed = translate_batch(model, code, label, batch, terms_in(terms, batch))
            for key, value in done.items():
                current[key] = value
                record[key] = {"en": digest(batch[key][0]), "out": digest(value)}
            for key, issues in failed.items():
                left_out += 1
                print(f"  left in English ({'; '.join(issues)}): {area}.{key}")
            print(f"  {len(done)}/{len(batch)} done")
        ordered = {k: current[k] for k in plan["wanted"] if k in current}
        write_json(LOCALES / code / f"{area}.json", unflatten(ordered))
        sources[area] = {k: record[k] for k in ordered if k in record}
        write_json(LOCALES / code / SOURCES_FILE, dict(sorted(sources.items())))
    return left_out, invalid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("languages", nargs="*", help="language codes, e.g. de es fr pt-BR")
    parser.add_argument("--area", action="append", help="only this area (repeatable), e.g. settings")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model (default {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true", help="say what would be translated")
    parser.add_argument("--check", action="store_true", help="report on every language folder; translate nothing")
    args = parser.parse_args()

    english = english_areas()
    if not english:
        print(f"No English files in {LOCALES / 'en'}", file=sys.stderr)
        return 1
    languages = args.languages
    if args.check or not languages:
        languages = languages or sorted(p.name for p in LOCALES.iterdir() if p.is_dir() and p.name != "en")
    if "en" in languages:
        print("English is the source; edit frontend/src/locales/en/ directly.", file=sys.stderr)
        return 1

    model = None
    if not (args.check or args.dry_run):
        try:
            models = installed_models()
        except (urllib.error.URLError, OSError) as error:
            print(f"Ollama is not reachable at {OLLAMA}: {error}", file=sys.stderr)
            return 1
        if args.model not in models and f"{args.model}:latest" not in models:
            print(f"{args.model} is not installed. Installed: {', '.join(sorted(models))}", file=sys.stderr)
            return 1
        model = args.model

    results = [run_language(code, english, args.area, model, args.dry_run or args.check) for code in languages]
    left_out = sum(r[0] for r in results)
    invalid = sum(r[1] for r in results)
    if left_out:
        print(f"{left_out} strings stay in English; run again to retry them.")
    if invalid:
        print(f"{invalid} hand-edited translations fail the checks (placeholders, tags, cut off); fix them by hand.")
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
