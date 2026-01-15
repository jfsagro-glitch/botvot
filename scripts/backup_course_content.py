"""
Create a backup snapshot of current lessons/tasks + referenced local media files.

What it does:
- Detects the active lessons.json used by the platform:
  1) <DB_DIR>/lessons.json (Drive-synced, persisted on Railway Volume)
  2) data/lessons.json
  3) seed_data/lessons.json
- Copies that lessons.json into backups folder with a timestamp.
- Parses lessons.json and copies all referenced local media by "path" into the backup snapshot,
  preserving relative paths.
- Produces a manifest with counts, missing media, and referenced Telegram file_ids.

Run (Windows / PowerShell):
  python scripts/backup_course_content.py

Optional args:
  --out backups/course_content_backup_YYYYmmdd-HHMMSS
  --zip  (also create .zip archive)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import sys

# Ensure project root is on sys.path when running as a script
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import Config  # noqa: E402


@dataclass
class BackupManifest:
    created_at_utc: str
    source_lessons_json: str
    lessons_count: int
    media_paths_found: int
    media_paths_missing: int
    telegram_file_ids_count: int
    missing_media: List[str]
    referenced_file_ids: List[str]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _find_active_lessons_json() -> Path:
    root = _project_root()
    db_dir = Path(Config.DATABASE_PATH).parent
    candidates = [
        db_dir / "lessons.json",
        root / "data" / "lessons.json",
        root / "seed_data" / "lessons.json",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    raise FileNotFoundError(f"lessons.json not found in: {candidates}")


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _collect_media_and_file_ids(lessons: Dict[str, Any]) -> Tuple[Set[str], Set[str]]:
    media_paths: Set[str] = set()
    file_ids: Set[str] = set()

    for _, lesson in lessons.items():
        if not isinstance(lesson, dict):
            continue
        media = lesson.get("media") or []
        if isinstance(media, list):
            for item in media:
                if not isinstance(item, dict):
                    continue
                p = (item.get("path") or "").strip()
                fid = (item.get("file_id") or "").strip()
                if p:
                    media_paths.add(p.replace("\\", "/"))
                if fid:
                    file_ids.add(fid)
    return media_paths, file_ids


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="Output directory for backup snapshot")
    ap.add_argument("--zip", action="store_true", help="Also create a .zip archive")
    args = ap.parse_args()

    root = _project_root()
    lessons_path = _find_active_lessons_json()
    lessons = _load_json(lessons_path)

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else (root / "backups" / f"course_content_backup_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save lessons.json
    _copy_file(lessons_path, out_dir / "lessons.json")

    # Also snapshot supporting JSONs if present (used by some lessons)
    for extra in ["lesson19_images.json", "lesson21_cards.json"]:
        p = lessons_path.parent / extra
        if p.exists():
            _copy_file(p, out_dir / extra)
        else:
            p2 = root / "data" / extra
            if p2.exists():
                _copy_file(p2, out_dir / extra)
            p3 = root / "seed_data" / extra
            if p3.exists():
                _copy_file(p3, out_dir / extra)

    media_paths, file_ids = _collect_media_and_file_ids(lessons)

    missing: List[str] = []
    found = 0
    for rel in sorted(media_paths):
        src = (root / rel).resolve()
        if src.exists() and src.is_file():
            _copy_file(src, out_dir / rel)
            found += 1
        else:
            missing.append(rel)

    manifest = BackupManifest(
        created_at_utc=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        source_lessons_json=str(lessons_path),
        lessons_count=len(lessons) if isinstance(lessons, dict) else 0,
        media_paths_found=found,
        media_paths_missing=len(missing),
        telegram_file_ids_count=len(file_ids),
        missing_media=missing,
        referenced_file_ids=sorted(file_ids),
    )

    with open(out_dir / "backup_manifest.json", "w", encoding="utf-8") as f:
        json.dump(asdict(manifest), f, ensure_ascii=False, indent=2)

    zip_path = None
    if args.zip:
        zip_path = shutil.make_archive(str(out_dir), "zip", root_dir=str(out_dir))

    print(f"Backup created: {out_dir}")
    if zip_path:
        print(f"Zip created: {zip_path}")
    print(f"Lessons: {manifest.lessons_count}, media_found={manifest.media_paths_found}, missing={manifest.media_paths_missing}, file_ids={manifest.telegram_file_ids_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

