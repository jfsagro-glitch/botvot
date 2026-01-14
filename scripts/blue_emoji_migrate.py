import json
from pathlib import Path


# Composite emoji sequences first (ZWJ / variation sequences)
SEQ_MAP: dict[str, str] = {
    "🏃\u200d♂️": "🟦",
    "🏃\u200d♂\ufe0f": "🟦",
    "🏃\u200d♂": "🟦",
    "🕵️\u200d♂️": "🧿",
    "🕵\ufe0f\u200d♂\ufe0f": "🧿",
    "🧙\u200d♂️": "🧿",
    "🧙\u200d♂\ufe0f": "🧿",
}


# Single emoji mapping (best-effort “blue palette” while keeping semantics)
EMOJI_MAP: dict[str, str] = {
    "♂": "🔵",
    "⚙": "🔹",
    "⚠": "🟦",
    "⚡": "🔷",
    "⛵": "🌊",
    "✅": "🔵",
    "✍": "🔹",
    "✨": "💙",
    "❌": "🔹",
    "❓": "🔵?",
    "❗": "🔵!",
    "➕": "🔵+",
    "🌅": "🌊",
    "🌊": "🌊",
    "🌍": "🌐",
    "🌐": "🌐",
    "🍽": "🟦",
    "🎆": "💙",
    "🎈": "💙",
    "🎉": "💙",
    "🎊": "💙",
    "🎙": "🟦",
    "🎛": "🟦",
    "🎣": "🐠",
    "🎥": "🟦",
    "🎨": "🟦",
    "🎪": "🟦",
    "🎬": "🟦",
    "🎭": "🟦",
    "🎮": "🟦",
    "🎯": "🧿",
    "🎲": "🟦",
    "🏁": "🟦",
    "🏃": "🟦",
    "🏄": "🟦",
    "🏙": "🌐",
    "🏹": "🧿",
    "🐠": "🐠",
    "🐾": "🟦",
    "👀": "🧿",
    "👤": "🟦",
    "👥": "🟦",
    "💎": "💎",
    "💝": "💙",
    "💡": "🔷",
    "💤": "🟦",
    "💨": "🟦",
    "💪": "🔷",
    "💫": "🔷",
    "💬": "🟦",
    "💭": "🟦",
    "💰": "🔷",
    "📅": "🟦",
    "📈": "🟦",
    "📊": "🟦",
    "📋": "🟦",
    "📍": "🟦",
    "📐": "🟦",
    "📕": "📘",
    "📖": "📘",
    "📚": "📘",
    "📝": "🔹",
    "📢": "🟦",
    "📤": "🟦",
    "📰": "🟦",
    "📱": "🟦",
    "📹": "🟦",
    "📺": "🟦",
    "📼": "🟦",
    "🔍": "🧿",
    "🔎": "🧿",
    "🔑": "🧿",
    "🔒": "🧿",
    "🔧": "🔹",
    "🔪": "🔹",
    "🔬": "🔹",
    "🔮": "🧿",
    "🔴": "🔵",
    "🕵": "🧿",
    "🗝": "🧿",
    "🗯": "🟦",
    "🗺": "🌐",
    "😊": "💙",
    "🚀": "🟦",
    "🛟": "🟦",
    "🤔": "🔵",
    "🤝": "💙",
    "🧙": "🧿",
    "🧠": "🧿",
    "🧭": "🧿",
    "🧱": "🟦",
    "🪝": "🟦",
    "🪡": "🟦",
    "🪶": "🟦",

    # Additional symbols discovered by broader scan
    "→": "🔷",
    "⬆": "🔷",
    "⬇": "🔷",
    "⏰": "🟦",
    "⏱": "🟦",
    "⏳": "🟦",
    "⭐": "🔷",
    "⭕": "🔵",
}


def transform_text(s: str) -> str:
    if not s:
        return s
    for k, v in SEQ_MAP.items():
        s = s.replace(k, v)
    for k, v in EMOJI_MAP.items():
        s = s.replace(k, v)
    return s


def walk(obj):
    if obj is None:
        return obj
    if isinstance(obj, str):
        return transform_text(obj)
    if isinstance(obj, list):
        return [walk(x) for x in obj]
    if isinstance(obj, dict):
        return {walk(k): walk(v) for k, v in obj.items()}
    return obj


def migrate_file(path: Path) -> int:
    before = path.read_text(encoding="utf-8")
    data = json.loads(before)
    new_data = walk(data)
    after = json.dumps(new_data, ensure_ascii=False, indent=2)
    if after != before:
        path.write_text(after + "\n", encoding="utf-8")
        return 1
    return 0


def main():
    targets = [
        Path("data/lessons.json"),
        Path("seed_data/lessons.json"),
    ]
    changed = 0
    for p in targets:
        if p.exists():
            changed += migrate_file(p)
    print("Changed files:", changed)


if __name__ == "__main__":
    main()

