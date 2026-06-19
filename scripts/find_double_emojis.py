import re
from pathlib import Path


ALLOWED = [
    "💬","☑️","✔️","➡️","⬅️","⬆️","⏺️","🎦","📶","🏧","💤","🌐","💠","❔","❕","💙","🤍","🖤",
    "🔎","🖌️","🖊️","🧷","📐","📖","📘","🗳️","📪","📨","🧿","💣","⚔️","💎","📡","⏱️","🧭",
    "🎛️","🎙️","📽️","📷","📸","📹","🎥","⌚️","⚓️","🪝","✈️","♟️","🎤","🎧","🎲","🎱","🧊","🌏","🌍",
]


def main() -> int:
    emoji_alt = "(?:" + "|".join(re.escape(e) for e in sorted(ALLOWED, key=len, reverse=True)) + ")"
    # Adjacent/space-separated emoji sequences, or emoji-bullet-emoji sequences
    pat = re.compile(rf"({emoji_alt})(?:[ \t]*[●•]?[ \t]*{emoji_alt})+")

    roots = ["bots", "utils", "services", "core", "data", "seed_data"]
    exts = {".py", ".json", ".md"}
    hits = 0

    for root in roots:
        p = Path(root)
        if not p.exists():
            continue
        for f in p.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in exts:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in pat.finditer(text):
                hits += 1
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 40)
                snippet = text[start:end].replace("\n", "\\n")
                print(f"{f.as_posix()}: {snippet}")
                if hits >= 200:
                    print("... truncated at 200 hits")
                    return 1

    return 0 if hits == 0 else 1


if __name__ == "__main__":
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())

