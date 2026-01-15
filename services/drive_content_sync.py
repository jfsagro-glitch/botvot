"""
Google Drive → lessons.json sync service.

Goal: allow editors to update course content in Google Drive without redeploying code.

Drive structure (recommended):
  <DRIVE_ROOT_FOLDER_ID>/
    day_00/
      lesson (Google Doc) OR lesson.txt OR lesson.html
      task  (Google Doc) OR task.txt  OR task.html
      meta.json (optional)
      media/ (optional subfolder) OR media files directly inside day_00
    day_01/
    ...

This service compiles all available days into a JSON compatible with existing LessonLoader,
and downloads media files to a persistent directory (Railway Volume) so CourseBot can send
them via FSInputFile when no Telegram file_id is present.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

from core.config import Config

logger = logging.getLogger(__name__)


GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass
class SyncResult:
    days_synced: int
    lessons_path: str
    media_files_downloaded: int
    warnings: List[str]


class DriveContentSync:
    def __init__(self):
        self.enabled = str(getattr(Config, "DRIVE_CONTENT_ENABLED", "0")).strip() == "1"
        self.root_folder_id = (getattr(Config, "DRIVE_ROOT_FOLDER_ID", "") or "").strip()
        self.media_dir = (getattr(Config, "DRIVE_MEDIA_DIR", "data/content_media") or "data/content_media").strip()

    def _admin_ready(self) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "DRIVE_CONTENT_ENABLED=0"
        if not self.root_folder_id:
            return False, "DRIVE_ROOT_FOLDER_ID is empty"
        if not (Config.GOOGLE_SERVICE_ACCOUNT_JSON or Config.GOOGLE_SERVICE_ACCOUNT_JSON_B64):
            return False, "Google service account creds are missing (GOOGLE_SERVICE_ACCOUNT_JSON[_B64])"
        return True, "ok"

    def _build_drive_client(self):
        # Lazy import to avoid hard dependency if feature disabled
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        raw_json = (Config.GOOGLE_SERVICE_ACCOUNT_JSON or "").strip()
        if not raw_json:
            b64 = (Config.GOOGLE_SERVICE_ACCOUNT_JSON_B64 or "").strip()
            raw_json = base64.b64decode(b64.encode("utf-8")).decode("utf-8")

        info = json.loads(raw_json)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    @staticmethod
    def _parse_day_from_name(name: str) -> Optional[int]:
        """
        Accepts: day_1, day-01, 01, 1, День 1, etc.
        """
        name = (name or "").strip()
        m = re.search(r"(?i)(?:day[_-]?|день\\s*)?(\\d{1,2})\\b", name)
        if not m:
            return None
        day = int(m.group(1))
        if 0 <= day <= 30:
            return day
        return None

    def _list_children(self, drive, parent_id: str) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        page_token = None
        while True:
            resp = drive.files().list(
                q=f"'{parent_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,modifiedTime,size)",
                pageToken=page_token,
                pageSize=1000,
            ).execute()
            files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return files

    def _download_text_file(self, drive, file_id: str, mime_type: str) -> str:
        """
        For Google Docs: export to text/plain.
        For other files: download bytes and decode utf-8.
        """
        if mime_type == GOOGLE_DOC_MIME:
            data = drive.files().export(fileId=file_id, mimeType="text/plain").execute()
            # google api returns bytes in this call
            if isinstance(data, str):
                return data
            return data.decode("utf-8", errors="replace")

        request = drive.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        from googleapiclient.http import MediaIoBaseDownload

        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return fh.getvalue().decode("utf-8", errors="replace")

    def _download_binary_file(self, drive, file_id: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        request = drive.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        from googleapiclient.http import MediaIoBaseDownload

        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        # Atomic write
        tmp = dest_path.with_suffix(dest_path.suffix + ".tmp")
        with open(tmp, "wb") as f:
            f.write(fh.getvalue())
        os.replace(tmp, dest_path)

    @staticmethod
    def _pick_named(files: List[Dict[str, Any]], base: str) -> Optional[Dict[str, Any]]:
        """
        Pick file whose name starts with base (lesson/task/meta) ignoring extension and case.
        Prefer: .html, .txt, Google Doc.
        """
        base_l = base.lower()
        candidates = []
        for f in files:
            name = (f.get("name") or "").strip()
            if not name:
                continue
            stem = name.rsplit(".", 1)[0].lower()
            if stem == base_l or stem.startswith(base_l):
                candidates.append(f)
        if not candidates:
            return None
        # prefer html, then txt, then doc
        def score(f: Dict[str, Any]) -> int:
            n = (f.get("name") or "").lower()
            mt = f.get("mimeType")
            if n.endswith(".html"):
                return 0
            if n.endswith(".txt"):
                return 1
            if mt == GOOGLE_DOC_MIME:
                return 2
            return 3
        candidates.sort(key=score)
        return candidates[0]

    def _target_lessons_path(self) -> Path:
        # Put lessons.json next to the DB, so Railway Volume persists it (/app/data)
        db_path = Path(Config.DATABASE_PATH)
        return db_path.parent / "lessons.json"

    def sync_now(self) -> SyncResult:
        ok, reason = self._admin_ready()
        if not ok:
            raise RuntimeError(f"Drive content sync not ready: {reason}")

        drive = self._build_drive_client()
        warnings: List[str] = []

        root_children = self._list_children(drive, self.root_folder_id)
        day_folders: List[Tuple[int, Dict[str, Any]]] = []
        for f in root_children:
            if f.get("mimeType") != FOLDER_MIME:
                continue
            day = self._parse_day_from_name(f.get("name", ""))
            if day is None:
                continue
            day_folders.append((day, f))
        day_folders.sort(key=lambda x: x[0])

        if not day_folders:
            raise RuntimeError("No day folders found in Drive root (expected day_00..day_30)")

        project_root = Path.cwd()
        media_root = (project_root / self.media_dir).resolve()

        compiled: Dict[str, Any] = {}
        media_downloaded = 0

        for day, folder in day_folders:
            folder_id = folder["id"]
            children = self._list_children(drive, folder_id)

            # If there's a "media" subfolder, include its contents too.
            media_folder = None
            for c in children:
                if c.get("mimeType") == FOLDER_MIME and (c.get("name") or "").lower() == "media":
                    media_folder = c
                    break
            media_children = self._list_children(drive, media_folder["id"]) if media_folder else []

            lesson_file = self._pick_named(children, "lesson")
            task_file = self._pick_named(children, "task")
            meta_file = self._pick_named(children, "meta")

            if not lesson_file:
                warnings.append(f"day {day}: missing lesson file")
                continue

            lesson_text = self._download_text_file(drive, lesson_file["id"], lesson_file.get("mimeType", ""))
            task_text = ""
            if task_file:
                task_text = self._download_text_file(drive, task_file["id"], task_file.get("mimeType", ""))

            meta: Dict[str, Any] = {}
            if meta_file and (meta_file.get("name") or "").lower().endswith(".json"):
                try:
                    meta_raw = self._download_text_file(drive, meta_file["id"], meta_file.get("mimeType", ""))
                    meta = json.loads(meta_raw)
                except Exception as e:
                    warnings.append(f"day {day}: meta.json invalid ({e})")

            # Collect media files (images/videos) from day folder + media subfolder
            media_items: List[Dict[str, Any]] = []
            media_sources = [c for c in (children + media_children) if c.get("mimeType") not in (FOLDER_MIME, GOOGLE_DOC_MIME)]

            for m in media_sources:
                name = (m.get("name") or "").strip()
                mt = (m.get("mimeType") or "").lower()
                if not name or not mt:
                    continue
                if mt.startswith("image/"):
                    media_type = "photo"
                elif mt.startswith("video/"):
                    media_type = "video"
                else:
                    continue

                safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
                dest = media_root / f"day_{day:02d}" / safe_name
                try:
                    self._download_binary_file(drive, m["id"], dest)
                    media_downloaded += 1
                    # Store path relative to project root, because CourseBot resolves it that way
                    rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                    media_items.append({"type": media_type, "path": rel_path})
                except Exception as e:
                    warnings.append(f"day {day}: failed to download media {name} ({e})")

            title = (meta.get("title") or "").strip() if isinstance(meta, dict) else ""
            if not title:
                title = f"День {day}"

            entry: Dict[str, Any] = {
                "day_number": day,
                "title": title,
                "text": (lesson_text or "").strip(),
                "task": (task_text or "").strip(),
            }
            if media_items:
                entry["media"] = media_items
            if isinstance(meta, dict) and "silent" in meta:
                entry["silent"] = bool(meta.get("silent"))

            compiled[str(day)] = entry

        if not compiled:
            raise RuntimeError("No lessons compiled (check Drive folder contents)")

        # Basic validation: ensure each lesson has text
        for k, v in compiled.items():
            if not (v.get("text") or "").strip():
                warnings.append(f"day {k}: empty lesson text")

        target = self._target_lessons_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(compiled, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)

        logger.info(f"✅ Drive sync wrote {len(compiled)} lessons to {target}")
        return SyncResult(
            days_synced=len(compiled),
            lessons_path=str(target),
            media_files_downloaded=media_downloaded,
            warnings=warnings,
        )

