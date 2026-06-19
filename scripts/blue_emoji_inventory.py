import json
import re
from pathlib import Path


# Broader emoji detector (covers most pictographs + key symbols)
# Note: still not perfect (Unicode emoji is complex), but good enough for audit.
EMOJI_RE = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u2190-\u21FF\u2B00-\u2BFF]")


def extract_emojis(obj, out: set[str]):
    if obj is None:
        return
    if isinstance(obj, str):
        for ch in obj:
            if EMOJI_RE.match(ch):
                out.add(ch)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            extract_emojis(k, out)
            extract_emojis(v, out)
        return
    if isinstance(obj, list):
        for it in obj:
            extract_emojis(it, out)
        return


def main():
    files = [
        Path("data/lessons.json"),
        Path("seed_data/lessons.json"),
        Path("data/lesson19_images.json"),
        Path("seed_data/lesson19_images.json"),
        Path("data/lesson21_cards.json"),
        Path("seed_data/lesson21_cards.json"),
    ]

    per_file: dict[str, set[str]] = {}
    all_emojis: set[str] = set()

    for p in files:
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        s: set[str] = set()
        extract_emojis(data, s)
        per_file[str(p)] = s
        all_emojis |= s

    print("Files scanned:", len(per_file))
    for k in sorted(per_file.keys()):
        print(f"- {k}: {len(per_file[k])} emojis")

    print("\nTotal unique emojis:", len(all_emojis))

    # Windows consoles often can't print emojis; write UTF-8 file for inspection.
    out_path = Path("bot_logs") if False else Path("emoji_inventory.txt")
    emoji_sorted = sorted(all_emojis)
    out_path.write_text("".join(emoji_sorted), encoding="utf-8")
    print(f"Wrote UTF-8 emoji list to: {out_path}")

    # Also print codepoints for debugging in ASCII-safe form
    cps = [f"U+{ord(ch):04X}" for ch in emoji_sorted]
    print("Codepoints:", " ".join(cps))


if __name__ == "__main__":
    main()

