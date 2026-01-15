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
import html
from html.parser import HTMLParser
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
        if not self.root_folder_id and not (Config.DRIVE_MASTER_DOC_ID or "").strip():
            return False, "Set DRIVE_ROOT_FOLDER_ID (folders mode) or DRIVE_MASTER_DOC_ID (single-doc mode)"
        if not (Config.GOOGLE_SERVICE_ACCOUNT_JSON or Config.GOOGLE_SERVICE_ACCOUNT_JSON_B64):
            return False, "Google service account creds are missing (GOOGLE_SERVICE_ACCOUNT_JSON[_B64])"
        return True, "ok"

    @staticmethod
    def _extract_drive_file_ids(text: str) -> List[str]:
        """
        Extract Drive file IDs from common URL forms:
          - https://drive.google.com/file/d/<id>/view
          - https://drive.google.com/open?id=<id>
          - https://docs.google.com/document/d/<id>/edit
          - https://drive.google.com/uc?id=<id>
        """
        if not text:
            return []
        ids = set()
        patterns = [
            r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]{10,})",
            r"https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]{10,})",
            r"https?://drive\.google\.com/uc\?id=([a-zA-Z0-9_-]{10,})",
            r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]{10,})",
        ]
        for p in patterns:
            for m in re.findall(p, text):
                ids.add(m)
        return list(ids)

    @staticmethod
    def _split_master_doc(text: str) -> Dict[int, Dict[str, str]]:
        """
        Split a master doc (plain text export) into per-day blocks.

        Expected markers in doc (Russian or English):
          - "День 0" ... "День 30"
          - "Day 0" ... "Day 30"

        Inside each day block:
          - Optional title line: "Заголовок: ..." or "Title: ..."
          - Task starts at a line beginning with "Задание:" / "Task:"
        """
        blocks: Dict[int, List[str]] = {}
        current_day: Optional[int] = None

        lines = (text or "").splitlines()
        day_header_re = re.compile(r"^\s*(?:День|Day)\s+(\d{1,2})\s*(?::\s*(.*))?$", re.IGNORECASE)

        for line in lines:
            m = day_header_re.match(line)
            if m:
                day = int(m.group(1))
                if 0 <= day <= 30:
                    current_day = day
                    blocks.setdefault(day, [])
                    # If header has title after colon, store it as first line hint
                    title_hint = (m.group(2) or "").strip()
                    if title_hint:
                        blocks[day].append(f"Заголовок: {title_hint}")
                    continue
            if current_day is not None:
                blocks[current_day].append(line)

        out: Dict[int, Dict[str, str]] = {}
        for day, bl in blocks.items():
            raw = "\n".join(bl).strip()
            if not raw:
                continue

            title = ""
            lesson = raw
            task = ""

            # title line
            for ln in bl[:10]:
                mm = re.match(r"^\s*(?:Заголовок|Title)\s*:\s*(.+)\s*$", ln, re.IGNORECASE)
                if mm:
                    title = mm.group(1).strip()
                    break

            # split task
            task_re = re.compile(r"^\s*(?:Задание|Task)\s*:\s*$", re.IGNORECASE)
            parts_lesson: List[str] = []
            parts_task: List[str] = []
            in_task = False
            for ln in bl:
                if task_re.match(ln):
                    in_task = True
                    continue
                (parts_task if in_task else parts_lesson).append(ln)
            lesson = "\n".join(parts_lesson).strip()
            task = "\n".join(parts_task).strip()

            out[day] = {"title": title, "lesson": lesson, "task": task}

        return out

    def _sync_from_master_doc(self, drive, warnings: List[str]) -> Tuple[Dict[str, Any], int]:
        master_id = (Config.DRIVE_MASTER_DOC_ID or "").strip()
        if not master_id:
            raise RuntimeError("DRIVE_MASTER_DOC_ID is empty")

        # Download master doc text
        master_text = self._download_text_file(drive, master_id, GOOGLE_DOC_MIME)
        master_text, w = self._sanitize_telegram_html(master_text or "")
        if w:
            warnings.extend([f"master_doc: {x}" for x in w])

        day_map = self._split_master_doc(master_text)
        if not day_map:
            raise RuntimeError("Could not find any 'День N' sections in master doc")

        project_root = Path.cwd()
        media_root = (project_root / self.media_dir).resolve()
        media_downloaded = 0

        compiled: Dict[str, Any] = {}
        for day, data in sorted(day_map.items(), key=lambda x: x[0]):
            title = (data.get("title") or "").strip() or f"День {day}"
            lesson_text = (data.get("lesson") or "").strip()
            task_text = (data.get("task") or "").strip()

            # Optional: download Drive-linked media referenced in the text/task
            media_items: List[Dict[str, Any]] = []
            for fid in self._extract_drive_file_ids(lesson_text + "\n" + task_text):
                try:
                    meta = drive.files().get(fileId=fid, fields="id,name,mimeType").execute()
                    mt = (meta.get("mimeType") or "").lower()
                    name = (meta.get("name") or f"file_{fid}").strip()
                    if mt.startswith("image/"):
                        media_type = "photo"
                    elif mt.startswith("video/"):
                        media_type = "video"
                    else:
                        continue
                    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
                    dest = media_root / f"day_{day:02d}" / safe_name
                    self._download_binary_file(drive, fid, dest)
                    media_downloaded += 1
                    rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                    media_items.append({"type": media_type, "path": rel_path})
                except Exception as e:
                    warnings.append(f"day {day}: failed to download linked media ({fid}): {e}")

            entry: Dict[str, Any] = {
                "day_number": day,
                "title": title,
                "text": lesson_text,
                "task": task_text,
            }
            if media_items:
                entry["media"] = media_items
            compiled[str(day)] = entry

        return compiled, media_downloaded

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
        m = re.search(r"(?i)(?:day[_-]?|день\s*)?(\d{1,2})\b", name)
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
    def _sanitize_telegram_html(text: str) -> Tuple[str, List[str]]:
        """
        Telegram HTML is a strict subset. Editors will type tags directly in Google Docs.
        This sanitizer keeps only safe Telegram tags and escapes everything else.

        Allowed tags (Telegram HTML): b/strong, i/em, u/ins, s/strike/del, code, pre,
        a (href only), tg-spoiler, blockquote.
        Also supports span class="tg-spoiler" (Telegram spoiler).
        """
        warnings: List[str] = []

        allowed = {
            "b", "strong",
            "i", "em",
            "u", "ins",
            "s", "strike", "del",
            "code", "pre",
            "a",
            "tg-spoiler",
            "blockquote",
            "span",
        }

        class _Sanitizer(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.out: List[str] = []

            def handle_data(self, data: str) -> None:
                self.out.append(data)

            def handle_entityref(self, name: str) -> None:
                self.out.append(f"&{name};")

            def handle_charref(self, name: str) -> None:
                self.out.append(f"&#{name};")

            def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
                tag_l = (tag or "").lower()
                if tag_l not in allowed:
                    warnings.append(f"removed tag <{tag}>")
                    self.out.append(html.escape(f"<{tag}>"))
                    return

                if tag_l == "a":
                    href = ""
                    for k, v in attrs:
                        if (k or "").lower() == "href" and v:
                            href = v
                            break
                    if not href:
                        warnings.append("a tag without href removed")
                        self.out.append(html.escape("<a>"))
                        return
                    safe_href = href.replace("\"", "&quot;")
                    self.out.append(f"<a href=\"{safe_href}\">")
                    return

                if tag_l == "span":
                    # allow only spoiler span
                    cls = ""
                    for k, v in attrs:
                        if (k or "").lower() == "class" and v:
                            cls = v
                            break
                    if cls != "tg-spoiler":
                        warnings.append("span tag without class=tg-spoiler removed")
                        self.out.append(html.escape("<span>"))
                        return
                    self.out.append("<span class=\"tg-spoiler\">")
                    return

                # other allowed tags with no attrs
                self.out.append(f"<{tag_l}>")

            def handle_endtag(self, tag: str) -> None:
                tag_l = (tag or "").lower()
                if tag_l not in allowed:
                    self.out.append(html.escape(f"</{tag}>"))
                    return
                if tag_l == "strong":
                    tag_l = "b"
                if tag_l == "em":
                    tag_l = "i"
                if tag_l == "ins":
                    tag_l = "u"
                if tag_l == "strike":
                    tag_l = "s"
                self.out.append(f"</{tag_l}>")

        parser = _Sanitizer()
        try:
            parser.feed(text or "")
            parser.close()
        except Exception as e:
            # If parsing fails, escape everything to avoid Telegram parse errors
            return html.escape(text or ""), [f"html parse error: {e}"]

        sanitized = "".join(parser.out)

        # If someone typed "1 < 2" it could be interpreted as a tag start; best-effort fix:
        # escape any remaining "<" that doesn't look like a tag start.
        sanitized = re.sub(r"<(?!/?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|a|tg-spoiler|blockquote|span)\b)", "&lt;", sanitized)

        return sanitized, warnings

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

        # Single-doc mode
        if (Config.DRIVE_MASTER_DOC_ID or "").strip():
            compiled, media_downloaded = self._sync_from_master_doc(drive, warnings)
            target = self._target_lessons_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(compiled, f, ensure_ascii=False, indent=2)
            os.replace(tmp, target)
            logger.info(f"✅ Drive master-doc sync wrote {len(compiled)} lessons to {target}")
            return SyncResult(
                days_synced=len(compiled),
                lessons_path=str(target),
                media_files_downloaded=media_downloaded,
                warnings=warnings,
            )

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

            # Telegram HTML sanitizer (editors type tags directly in Google Docs)
            lesson_text, w1 = self._sanitize_telegram_html(lesson_text or "")
            if w1:
                warnings.extend([f"day {day}: {w}" for w in w1])
            if task_text:
                task_text, w2 = self._sanitize_telegram_html(task_text or "")
                if w2:
                    warnings.extend([f"day {day}: {w}" for w in w2])

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

