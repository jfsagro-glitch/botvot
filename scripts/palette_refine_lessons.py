import json
import re
from pathlib import Path


def refine_text(s: str) -> str:
    if not s:
        return s

    # Normalize "blue question" / "blue exclamation" placeholders to actual punctuation emojis
    s = s.replace("🔵?", "🔵❔")
    s = s.replace("🔵!", "🔵❕")
    s = s.replace("🔵+", "🔵➕")

    # Collapse accidental duplicates from earlier migrations
    # - IMPORTANT: only collapse across spaces/tabs (NOT newlines) to preserve formatting.
    s = re.sub(r"(🟦[ \t]*){2,}", "🟦", s)
    s = re.sub(r"(🔷[ \t]*){2,}", "🔷", s)
    s = re.sub(r"(🧿[ \t]*){2,}", "🧿", s)
    s = re.sub(r"(🩵[ \t]*){2,}", "🩵", s)
    s = re.sub(r"(🌊[ \t]*){2,}", "🌊", s)
    s = re.sub(r"(⚫[ \t]*){2,}", "⚫ ", s)
    s = re.sub(r"(⚪️[ \t]*){2,}", "⚪️", s)
    s = re.sub(r"(⚪[ \t]*){2,}", "⚪", s)

    # Contextual markers: IMPORTANT / WARNING → gray/black emphasis
    # (Keep meaning; avoid yellow/red.)
    s = re.sub(r"(^|\n)🟦️?\s*Важно", r"\1🩶❕ Важно", s)
    s = re.sub(r"(^|\n)🟦️?\s*Внимание", r"\1🩶❕ Внимание", s)
    s = re.sub(r"(^|\n)(Осторожно[,!:]?)", r"\1⚫ \2", s)

    # White palette (allowed): silence/quiet/day-off vibe
    s = s.replace("\n\n🟦 Сегодня воскресенье, день тишины.", "\n\n⚪️ Сегодня воскресенье, день тишины.")

    # De-emoji: remove redundant markers where meaning already conveyed by text nearby
    s = re.sub(r"🔵❔\s*●\s*🔵❔\s*", "🔵❔ ● ", s)  # bullet marker duplication
    s = re.sub(r"Только\s*🔵❔\s*вопрос", "Только вопрос", s)
    s = re.sub(r"Просто\s*🔵❔\s*задайте", "Просто задайте", s)

    # Common “double marker” phrases → keep one, keep context
    s = re.sub(r"🟦\s*Подведите\s*🟦\s*итоги", "🟦 Подведите итоги", s)
    s = re.sub(r"🟦\s*А еще\s*🟦\s*сегодня", "🟦 А еще сегодня", s, flags=re.IGNORECASE)
    s = re.sub(r"🔷\s*Сделайте\s*🔷\s*вывод", "🔷 Сделайте вывод", s, flags=re.IGNORECASE)

    # Trim decorative trailing clusters (keep one tone emoji)
    s = re.sub(r"([.!?…])\s*🔷\s*🩵\s*$", r"\1 🩵", s)

    # Tool headings: keep semantics but allow gray palette
    # Example blocks use "🔹 ОТВЁРТКА"/"🔹 НОЖ" etc.
    tool_map = {
        "ОТВЁРТКА": "🩶🔧 ОТВЁРТКА",
        "НОЖ": "🩶🔪 НОЖ",
        "КЛЮЧ": "🩶🗝 КЛЮЧ",
        "СКАЛЬПЕЛЬ": "🩶🔪 СКАЛЬПЕЛЬ",
    }
    for tool, repl in tool_map.items():
        s = re.sub(rf"(^|\n)🔹\s+{re.escape(tool)}\b", rf"\1{repl}", s)

    # "copying locked" / "closed" → subtle dark marker
    s = re.sub(
        r"(?<!⚫\s)Копирование в группе будет закрыто",
        "⚫ Копирование в группе будет закрыто",
        s,
    )

    # Final whitespace cleanup (do not touch newlines, only trailing spaces)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"[ \t]+$", "", s)

    # Ensure there's a space between leading marker-emoji and the following word/number
    # (This prevents "🟦Сегодня" after collapsing duplicates like "🟦 🟦 Сегодня".)
    s = re.sub(r"(🔵❔)([0-9A-Za-zА-Яа-яЁё])", r"\1 \2", s)
    s = re.sub(r"(🔵❕)([0-9A-Za-zА-Яа-яЁё])", r"\1 \2", s)
    s = re.sub(r"([🟦🔷🧿🩵🌊⚪️⚪⚫🩶❕🩶])(?!\s)([0-9A-Za-zА-Яа-яЁё])", r"\1 \2", s)

    return s


def walk(obj):
    if obj is None:
        return obj
    if isinstance(obj, str):
        return refine_text(obj)
    if isinstance(obj, list):
        return [walk(x) for x in obj]
    if isinstance(obj, dict):
        return {k: walk(v) for k, v in obj.items()}
    return obj


def migrate(path: Path) -> bool:
    before = path.read_text(encoding="utf-8")
    data = json.loads(before)
    new_data = walk(data)
    after = json.dumps(new_data, ensure_ascii=False, indent=2) + "\n"
    if after != before:
        path.write_text(after, encoding="utf-8")
        return True
    return False


def main():
    changed = 0
    for p in [Path("data/lessons.json"), Path("seed_data/lessons.json")]:
        if p.exists() and migrate(p):
            changed += 1
    print("changed_files", changed)


if __name__ == "__main__":
    main()

