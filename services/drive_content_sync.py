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
import shutil
from datetime import datetime, timezone
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
    @staticmethod
    def _find_drive_links_with_positions(text: str) -> List[Dict[str, str]]:
        """
        Find all Drive links in text with their positions and file/folder IDs.
        Returns list of dicts with 'url' (original from text), 'file_id' (or 'folder_id'), 'start', 'end', 'is_folder'.
        """
        if not text:
            return []
        
        links = []
        # Patterns to match various Google Drive URL formats
        # Important: we capture the FULL original URL from text for replacement
        patterns = [
            # Folder link: https://drive.google.com/drive/folders/FOLDER_ID?usp=drive_link
            (r"https?://drive\.google\.com/drive/folders/([a-zA-Z0-9_-]{10,})(?:/[^?\s]*)?(?:\?[^\s]*)?", lambda m: m.group(0), True),
            # Standard file link: https://drive.google.com/file/d/FILE_ID/view?usp=drive_link
            (r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]{10,})(?:/[^?\s]*)?(?:\?[^\s]*)?", lambda m: m.group(0), False),
            # Open link: https://drive.google.com/open?id=FILE_ID
            (r"https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]{10,})(?:&[^\s]*)?", lambda m: m.group(0), False),
            # UC link: https://drive.google.com/uc?id=FILE_ID
            (r"https?://drive\.google\.com/uc\?id=([a-zA-Z0-9_-]{10,})(?:&[^\s]*)?", lambda m: m.group(0), False),
            # Document link: https://docs.google.com/document/d/FILE_ID/...
            (r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]{10,})(?:/[^\s]*)?", lambda m: m.group(0), False),
        ]
        
        for pattern, url_extractor, is_folder in patterns:
            for match in re.finditer(pattern, text):
                item_id = match.group(1)
                # Use the FULL original URL from text (including /view?usp=drive_link etc.)
                original_url = url_extractor(match)
                links.append({
                    "url": original_url,  # Original URL from text for exact replacement
                    "file_id": item_id,  # For folders, this is actually folder_id
                    "folder_id": item_id if is_folder else None,  # Explicit folder_id field
                    "is_folder": is_folder,
                    "start": match.start(),
                    "end": match.end()
                })
        
        # Remove duplicates (same file_id/folder_id, keep first occurrence)
        seen_ids = set()
        unique_links = []
        for link in links:
            link_key = (link["file_id"], link.get("is_folder", False))
            if link_key not in seen_ids:
                seen_ids.add(link_key)
                unique_links.append(link)
        
        return unique_links

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
            # Support both formats:
            #   "Задание:" (on its own line)
            #   "Задание: текст задания" (text on same line)
            task_re = re.compile(r"^\s*(?:Задание|Task)\s*:\s*(.*)$", re.IGNORECASE)
            parts_lesson: List[str] = []
            parts_task: List[str] = []
            in_task = False
            for ln in bl:
                m = task_re.match(ln)
                if m:
                    in_task = True
                    # If there's text on the same line after "Задание:", add it to task
                    task_text_on_line = (m.group(1) or "").strip()
                    if task_text_on_line:
                        parts_task.append(task_text_on_line)
                    continue
                (parts_task if in_task else parts_lesson).append(ln)
            lesson = "\n".join(parts_lesson).strip()
            task = "\n".join(parts_task).strip()

            # Split lesson into posts:
            # 1. By manual markers: ---POST--- or [POST] or ---
            # 2. Automatically by length (>4000 chars)
            lesson_posts = DriveContentSync._split_lesson_into_posts(lesson)
            
            # If lesson was split into multiple posts, store as list; otherwise as string (backward compatible)
            if len(lesson_posts) > 1:
                out[day] = {"title": title, "lesson": lesson_posts, "task": task}
            else:
                out[day] = {"title": title, "lesson": lesson_posts[0] if lesson_posts else "", "task": task}

        return out
    
    @staticmethod
    def _split_lesson_into_posts(lesson_text: str, max_length: int = 4000) -> List[str]:
        """
        Split lesson text into multiple posts.
        
        Supports:
        1. Manual markers: ---POST---, [POST], --- (on its own line)
        2. Automatic splitting by length (>4000 chars) at paragraph boundaries
        
        Args:
            lesson_text: Full lesson text
            max_length: Maximum length per post (default 4000, Telegram limit is 4096)
        
        Returns:
            List of post texts
        """
        if not lesson_text or not lesson_text.strip():
            return [""]
        
        # Step 1: Split by manual markers
        # Support: ---POST---, [POST], [любые квадратные кавычки], --- (on its own line)
        # Split by lines first to find markers
        lines = lesson_text.split('\n')
        posts = []
        current_post = []
        
        for line in lines:
            # Check if line is a post marker
            # Match: ---POST---, [POST], [любые квадратные кавычки на отдельной строке], ---
            # Также проверяем, если квадратные скобки в начале или конце строки с текстом
            line_stripped = line.strip()
            is_marker = (
                re.match(r'^\s*(?:---POST---|---)\s*$', line, re.IGNORECASE) or
                re.match(r'^\s*\[.*?\]\s*$', line) or  # Любые квадратные кавычки на отдельной строке
                (line_stripped.startswith('[') and line_stripped.endswith(']') and len(line_stripped) < 50)  # Короткие маркеры вроде [POST]
            )
            
            if is_marker:
                # Save current post if it has content
                if current_post:
                    post_text = '\n'.join(current_post).strip()
                    if post_text:
                        posts.append(post_text)
                    current_post = []
                # Marker line itself is NOT included in any post
            else:
                # Проверяем, есть ли квадратные скобки внутри строки (не только маркеры)
                # Если строка содержит [POST] или подобные маркеры, разделяем по ним
                if '[' in line and ']' in line:
                    # Ищем маркеры внутри строки
                    marker_pattern = r'\[(POST|ДОПОЛНЕНИЕ|BLOCK|БЛОК|POST\d*)\]'
                    matches = list(re.finditer(marker_pattern, line, re.IGNORECASE))
                    if matches:
                        # Разделяем строку по маркерам
                        last_pos = 0
                        for match in matches:
                            # Добавляем текст до маркера
                            if match.start() > last_pos:
                                text_before = line[last_pos:match.start()].strip()
                                if text_before:
                                    current_post.append(text_before)
                            # Сохраняем текущий пост и начинаем новый
                            if current_post:
                                post_text = '\n'.join(current_post).strip()
                                if post_text:
                                    posts.append(post_text)
                                current_post = []
                            last_pos = match.end()
                        # Добавляем текст после последнего маркера
                        if last_pos < len(line):
                            text_after = line[last_pos:].strip()
                            if text_after:
                                current_post.append(text_after)
                    else:
                        # Нет маркеров внутри, просто добавляем строку
                        current_post.append(line)
                else:
                    current_post.append(line)
        
        # Add last post if any
        if current_post:
            post_text = '\n'.join(current_post).strip()
            if post_text:
                posts.append(post_text)
        
        # If we found markers and split, return posts
        if len(posts) > 1:
            return posts
        
        # Step 2: If no manual markers, check length and split automatically if needed
        if len(lesson_text) <= max_length:
            return [lesson_text.strip()]
        
        # Auto-split by paragraphs, trying to keep posts under max_length
        posts = []
        paragraphs = lesson_text.split('\n\n')
        current_post = []
        current_length = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            para_length = len(para)
            
            # If single paragraph exceeds max_length, split it by lines
            if para_length > max_length:
                # Save current post if any
                if current_post:
                    posts.append('\n\n'.join(current_post))
                    current_post = []
                    current_length = 0
                
                # Split long paragraph by lines
                lines = para.split('\n')
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    line_length = len(line)
                    if current_length + line_length + 2 > max_length:  # +2 for \n\n
                        if current_post:
                            posts.append('\n\n'.join(current_post))
                            current_post = []
                            current_length = 0
                    
                    current_post.append(line)
                    current_length += line_length + 2  # +2 for \n\n
            else:
                # Check if adding this paragraph would exceed max_length
                if current_post and current_length + para_length + 2 > max_length:  # +2 for \n\n
                    posts.append('\n\n'.join(current_post))
                    current_post = []
                    current_length = 0
                
                current_post.append(para)
                current_length += para_length + 2  # +2 for \n\n
        
        # Add remaining post
        if current_post:
            posts.append('\n\n'.join(current_post))
        
        return posts if posts else [lesson_text.strip()]

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
            lesson_raw = data.get("lesson") or ""
            # Support multi-post lessons returned as list
            if isinstance(lesson_raw, list):
                lesson_text = "\n\n".join([str(x) for x in lesson_raw]).strip()
            else:
                lesson_text = str(lesson_raw).strip()
            task_text = (data.get("task") or "").strip()

            # Process Drive-linked media referenced in the text/task
            # Replace links with markers and download files
            media_items: List[Dict[str, Any]] = []
            media_markers: Dict[str, Dict[str, Any]] = {}  # marker_id -> media_info
            
            # Find all Drive links in lesson and task text
            # ВАЖНО: В режиме master doc intro_text и about_me_text извлекаются из самого текста урока
            # Они не хранятся отдельно в метаданных, поэтому обрабатываем только lesson_text и task_text
            combined_text = lesson_text + "\n" + task_text
            
            # В режиме master doc intro_text и about_me_text могут быть в тексте урока,
            # но они будут обработаны вместе с lesson_text при обработке ссылок
            intro_text = ""
            about_me_text = ""
            
            # Special logging for day 0 to debug missing links
            if day == 0:
                logger.info(f"   🔍 DEBUG Day 0: lesson_text length: {len(lesson_text)}, task_text length: {len(task_text)}")
                logger.info(f"   🔍 DEBUG Day 0: lesson_text preview (first 500 chars): {lesson_text[:500]}")
                logger.info(f"   🔍 DEBUG Day 0: task_text preview (first 500 chars): {task_text[:500]}")
                # Check if link is in text but not found by pattern
                if "drive.google.com" in combined_text.lower() or "1XpI71z0vSm6uK1C8krBsBFrUwMSPNzXL" in combined_text:
                    logger.warning(f"   ⚠️ DEBUG Day 0: Found 'drive.google.com' or file_id in text, but pattern didn't match!")
                    # Try to find the exact string
                    all_drive_mentions = re.findall(r'[^\s]*drive\.google\.com[^\s]*', combined_text, re.IGNORECASE)
                    if all_drive_mentions:
                        logger.warning(f"   ⚠️ DEBUG Day 0: Found drive.google.com mentions: {all_drive_mentions[:5]}")
                # Check for file ID pattern in text (might be just the ID without full URL)
                file_id_pattern = re.findall(r'[a-zA-Z0-9_-]{25,}', combined_text)
                if file_id_pattern:
                    logger.warning(f"   ⚠️ DEBUG Day 0: Found potential file IDs in text: {file_id_pattern[:3]}")
                # Check if "000 Шерлок 3.mp4" is in text - this might be a link that was already processed
                if "000 Шерлок 3.mp4" in combined_text or "Шерлок" in combined_text:
                    logger.warning(f"   ⚠️ DEBUG Day 0: Found 'Шерлок' in text - checking if it's a link format")
                    # Try to find any URL-like patterns near "Шерлок"
                    url_patterns = re.findall(r'https?://[^\s]+', combined_text)
                    if url_patterns:
                        logger.warning(f"   ⚠️ DEBUG Day 0: Found URL patterns in text: {url_patterns[:3]}")
            
            drive_links = self._find_drive_links_with_positions(combined_text)
            
            logger.info(f"   📎 Day {day}: Found {len(drive_links)} Drive links in text")
            if drive_links:
                for link in drive_links:
                    logger.info(f"   📎   - Link: {link['url'][:60]}... (file_id: {link['file_id']})")
            elif day == 0:
                logger.warning(f"   ⚠️ Day 0: No Drive links found! This may indicate the link format is different or link is in a different field")
                # Special handling for day 0: if we see "000 Шерлок 3.mp4" but no link, 
                # try to find the file by name in Drive and create a marker manually
                if "000 Шерлок 3.mp4" in combined_text or "Шерлок" in combined_text.lower():
                    logger.warning(f"   ⚠️ Day 0: Found 'Шерлок' in text but no Drive link. Attempting to find file by name...")
                    # Known file ID for day 0 video (from user's previous message)
                    known_file_id = "1XpI71z0vSm6uK1C8krBsBFrUwMSPNzXL"
                    try:
                        meta = drive.files().get(fileId=known_file_id, fields="id,name,mimeType,modifiedTime,size").execute()
                        mt = (meta.get("mimeType") or "").lower()
                        name = (meta.get("name") or "").strip()
                        if mt.startswith("video/"):
                            logger.info(f"   ✅ Day 0: Found video file by known ID: {name} (MIME: {mt})")
                            safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
                            dest = media_root / f"day_00" / safe_name
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            
                            should_skip = self._should_skip_download(dest, meta.get("size"), meta.get("modifiedTime"))
                            if not should_skip or not dest.exists():
                                logger.info(f"   📎   Downloading day 0 video: {name} -> {dest}")
                                self._download_binary_file(drive, known_file_id, dest)
                                media_downloaded += 1
                            else:
                                logger.info(f"   📎   Day 0 video already exists: {dest}")
                            
                            rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                            marker_id = f"MEDIA_{known_file_id}_0"
                            media_markers[marker_id] = {
                                "type": "video",
                                "path": rel_path,
                                "file_id": known_file_id,
                                "name": name
                            }
                            logger.info(f"   ✅ Created media marker for day 0: [{marker_id}]")
                            
                            # Replace "000 Шерлок 3.mp4" with marker in text
                            if "000 Шерлок 3.mp4" in lesson_text:
                                lesson_text = lesson_text.replace("000 Шерлок 3.mp4", f"[{marker_id}]")
                                logger.info(f"   ✅ Replaced '000 Шерлок 3.mp4' with marker in lesson_text")
                            elif "Шерлок" in lesson_text.lower():
                                # Try to find and replace the line containing "Шерлок"
                                lines = lesson_text.split("\n")
                                for i, line in enumerate(lines):
                                    if "Шерлок" in line.lower() and ".mp4" in line.lower():
                                        lines[i] = f"[{marker_id}]"
                                        lesson_text = "\n".join(lines)
                                        logger.info(f"   ✅ Replaced line containing 'Шерлок' with marker in lesson_text")
                                        break
                        else:
                            logger.warning(f"   ⚠️ Day 0: File {known_file_id} is not a video (MIME: {mt})")
                    except Exception as e:
                        logger.error(f"   ❌ Day 0: Failed to process known file ID {known_file_id}: {e}")
            
            # Process links in reverse order to preserve positions when replacing
            drive_links.sort(key=lambda x: x["start"], reverse=True)
            
            processed_links = 0
            skipped_links = 0
            error_links = 0
            
            for link_info in drive_links:
                fid = link_info["file_id"]
                link_url = link_info["url"]
                is_folder = link_info.get("is_folder", False)
                
                try:
                    if is_folder:
                        # Handle folder: get all files in folder and process each as media
                        folder_id = link_info.get("folder_id") or fid
                        logger.info(f"   📁 Processing Drive folder: {link_url[:60]}... (folder_id: {folder_id})")
                        
                        # Get all files in the folder
                        folder_files = self._list_children(drive, folder_id)
                        logger.info(f"   📁   Found {len(folder_files)} items in folder")
                        
                        if not folder_files:
                            logger.warning(f"   ⚠️ Folder {folder_id} is empty or inaccessible")
                            skipped_links += 1
                            continue
                        
                        # Collect all markers for files in this folder
                        folder_markers = []
                        folder_media_count = 0
                        
                        for folder_file in folder_files:
                            file_id = folder_file.get("id")
                            file_name = folder_file.get("name", f"file_{file_id}").strip()
                            file_mime = (folder_file.get("mimeType") or "").lower()
                            
                            logger.info(f"   📁   Processing folder item: {file_name} (MIME: {file_mime})")
                            
                            # Only process media files (images/videos)
                            if file_mime.startswith("image/"):
                                media_type = "photo"
                            elif file_mime.startswith("video/"):
                                media_type = "video"
                            else:
                                logger.info(f"   📁   Skipping non-media file in folder: {file_name} (MIME: {file_mime})")
                                continue
                            
                            safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", file_name)
                            dest = media_root / f"day_{day:02d}" / safe_name
                            
                            # Ensure destination directory exists
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            
                            should_skip = self._should_skip_download(dest, folder_file.get("size"), folder_file.get("modifiedTime"))
                            file_exists = dest.exists()
                            logger.info(f"   📁     Destination: {dest}, File exists: {file_exists}, Should skip: {should_skip}")
                            
                            if not should_skip or not file_exists:
                                if not file_exists:
                                    logger.info(f"   📁     File doesn't exist, downloading: {file_name} -> {dest}")
                                else:
                                    logger.info(f"   📁     File outdated or size mismatch, re-downloading: {file_name} -> {dest}")
                                self._download_binary_file(drive, file_id, dest)
                                media_downloaded += 1
                                logger.info(f"   ✅ Downloaded media file from folder: {file_name} (total downloaded: {media_downloaded})")
                            else:
                                logger.info(f"   📁     File already exists and up-to-date, skipping download: {dest}")
                                skipped_links += 1
                            
                            processed_links += 1
                            rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                            
                            # Create marker for this file
                            marker_id = f"MEDIA_{file_id}_{len(media_markers)}"
                            media_markers[marker_id] = {
                                "type": media_type,
                                "path": rel_path,
                                "file_id": file_id,
                                "name": file_name
                            }
                            folder_markers.append(marker_id)
                            folder_media_count += 1
                            logger.info(f"   ✅ Created media marker: [{marker_id}] for file {file_name} (path: {rel_path})")
                            
                            media_items.append({"type": media_type, "path": rel_path, "marker_id": marker_id})
                        
                        # Replace folder link with all markers (one per line or comma-separated)
                        if folder_markers:
                            # Replace folder URL with all markers, one per line
                            markers_text = "\n".join([f"[{m}]" for m in folder_markers])
                            replaced_in_lesson = False
                            replaced_in_task = False
                            if link_url in lesson_text:
                                lesson_text = lesson_text.replace(link_url, markers_text)
                                replaced_in_lesson = True
                                logger.info(f"   ✅ Replaced Drive folder link in lesson_text with {len(folder_markers)} markers")
                            if link_url in task_text:
                                task_text = task_text.replace(link_url, markers_text)
                                replaced_in_task = True
                                logger.info(f"   ✅ Replaced Drive folder link in task_text with {len(folder_markers)} markers")
                            
                            if not replaced_in_lesson and not replaced_in_task:
                                logger.warning(f"   ⚠️ Drive folder link not found in lesson_text or task_text: {link_url[:60]}...")
                            
                            logger.info(f"   📁 Folder processed: {folder_media_count} media files, {len(folder_markers)} markers created")
                        else:
                            logger.warning(f"   ⚠️ No media files found in folder {folder_id}")
                            skipped_links += 1
                    else:
                        # Handle single file (existing logic)
                        logger.info(f"   📎 Processing Drive link: {link_url[:60]}... (file_id: {fid})")
                        
                        # Special handling for day 1 video: 1TQMTaSEhWgvJmrE9MJkh9BGsNCyRNtYO
                        if day == 1 and fid == "1TQMTaSEhWgvJmrE9MJkh9BGsNCyRNtYO":
                            logger.info(f"   📎   Special handling for day 1 video: {fid}")
                        
                        meta = drive.files().get(fileId=fid, fields="id,name,mimeType,modifiedTime,size").execute()
                        mt = (meta.get("mimeType") or "").lower()
                        name = (meta.get("name") or f"file_{fid}").strip()
                        
                        logger.info(f"   📎   File name: {name}, MIME type: {mt}")
                        
                        if mt.startswith("image/"):
                            media_type = "photo"
                        elif mt.startswith("video/"):
                            media_type = "video"
                        else:
                            logger.info(f"   📎   Skipping non-media file: {name} (MIME: {mt})")
                            skipped_links += 1
                            continue  # Skip non-media files
                        
                        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
                        dest = media_root / f"day_{day:02d}" / safe_name
                        
                        # Ensure destination directory exists
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        
                        should_skip = self._should_skip_download(dest, meta.get("size"), meta.get("modifiedTime"))
                        file_exists = dest.exists()
                        logger.info(f"   📎   Destination: {dest}, File exists: {file_exists}, Should skip: {should_skip}")
                        
                        if not should_skip or not file_exists:
                            if not file_exists:
                                logger.info(f"   📎   File doesn't exist, downloading: {name} -> {dest}")
                            else:
                                logger.info(f"   📎   File outdated or size mismatch, re-downloading: {name} -> {dest}")
                            self._download_binary_file(drive, fid, dest)
                            media_downloaded += 1
                            logger.info(f"   ✅ Downloaded media file: {name} (total downloaded: {media_downloaded})")
                        else:
                            logger.info(f"   📎   File already exists and up-to-date, skipping download: {dest}")
                            # Count existing files as "processed" for reporting
                            skipped_links += 1
                        
                        processed_links += 1
                        rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                        
                        # CRITICAL: Create marker ALWAYS, even if file was skipped
                        # The marker is needed for inline insertion regardless of download status
                        marker_id = f"MEDIA_{fid}_{len(media_markers)}"
                        media_markers[marker_id] = {
                            "type": media_type,
                            "path": rel_path,
                            "file_id": fid,
                            "name": name
                        }
                        logger.info(f"   ✅ Created media marker: [{marker_id}] for file {name} (path: {rel_path})")
                        
                        # Replace link in text with marker
                        # Use the original URL from text (link_url) for exact replacement
                        marker_placeholder = f"[{marker_id}]"
                        # Replace all occurrences of the link URL in both texts, intro_text, and about_me_text
                        replaced_in_lesson = False
                        replaced_in_task = False
                        replaced_in_intro = False
                        replaced_in_about_me = False
                        
                        if link_url in lesson_text:
                            lesson_text = lesson_text.replace(link_url, marker_placeholder)
                            replaced_in_lesson = True
                            logger.info(f"   ✅ Replaced Drive link in lesson_text: {link_url[:60]}... -> [{marker_id}]")
                        if link_url in task_text:
                            task_text = task_text.replace(link_url, marker_placeholder)
                            replaced_in_task = True
                            logger.info(f"   ✅ Replaced Drive link in task_text: {link_url[:60]}... -> [{marker_id}]")
                        if intro_text and link_url in intro_text:
                            intro_text = intro_text.replace(link_url, marker_placeholder)
                            replaced_in_intro = True
                            logger.info(f"   ✅ Replaced Drive link in intro_text: {link_url[:60]}... -> [{marker_id}]")
                        if about_me_text and link_url in about_me_text:
                            about_me_text = about_me_text.replace(link_url, marker_placeholder)
                            replaced_in_about_me = True
                            logger.info(f"   ✅ Replaced Drive link in about_me_text: {link_url[:60]}... -> [{marker_id}]")
                        
                        if not replaced_in_lesson and not replaced_in_task and not replaced_in_intro and not replaced_in_about_me:
                            logger.warning(f"   ⚠️ Drive link not found in any text field: {link_url[:60]}...")
                            logger.warning(f"   ⚠️ This may indicate the link format changed or was already replaced")
                        
                        media_items.append({"type": media_type, "path": rel_path, "marker_id": marker_id})
                except Exception as e:
                    error_links += 1
                    error_msg = f"day {day}: failed to download linked media ({fid}): {e}"
                    logger.error(f"   ❌ {error_msg}")
                    warnings.append(error_msg)
            
            if drive_links:
                logger.info(f"   📎 Day {day} summary: {processed_links} processed, {skipped_links} skipped, {error_links} errors, {media_downloaded} downloaded")

            # Split lesson into posts by square brackets (if not already split)
            lesson_posts = DriveContentSync._split_lesson_into_posts(lesson_text)
            
            entry: Dict[str, Any] = {
                "day_number": day,
                "title": title,
                "text": lesson_posts if len(lesson_posts) > 1 else (lesson_posts[0] if lesson_posts else ""),
                "task": task_text,
            }
            
            # В режиме master doc intro_text и about_me_text не извлекаются отдельно
            # Они могут быть частью lesson_text, но обрабатываются вместе с ним
            
            if media_items:
                entry["media"] = media_items
            # CRITICAL: Store media markers for inline insertion
            # Always store markers if they exist, even if no files were downloaded
            if media_markers:
                entry["media_markers"] = media_markers
                logger.info(f"   ✅ Stored {len(media_markers)} media_markers in entry for day {day}")
                for marker_id in media_markers.keys():
                    logger.info(f"   📎     - {marker_id}")
            else:
                logger.warning(f"   ⚠️ No media_markers for day {day} (drive_links found: {len(drive_links)})")
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
    def _parse_rfc3339(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            # Google Drive returns RFC3339, often with "Z"
            v = value.replace("Z", "+00:00")
            return datetime.fromisoformat(v)
        except Exception:
            return None

    @classmethod
    def _should_skip_download(cls, dest_path: Path, remote_size: Optional[str], remote_mtime: Optional[str]) -> bool:
        if not dest_path.exists():
            return False
        try:
            if remote_size:
                size = int(remote_size)
                if size > 0 and dest_path.stat().st_size != size:
                    return False
            remote_dt = cls._parse_rfc3339(remote_mtime)
            if remote_dt:
                local_dt = datetime.fromtimestamp(dest_path.stat().st_mtime, tz=timezone.utc)
                if local_dt >= remote_dt:
                    return True
            # If size matches and no mtime, treat as cached
            if remote_size:
                return True
        except Exception:
            return False
        return False

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

    def _backup_file_if_exists(self, target: Path) -> Optional[Path]:
        """
        Create a timestamped backup copy next to the target, if it exists.
        Returns backup path if created.
        """
        try:
            if not target.exists() or not target.is_file():
                return None
            backups_dir = target.parent / "content_backups"
            backups_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            backup_path = backups_dir / f"{target.stem}.{ts}{target.suffix}"
            shutil.copy2(target, backup_path)
            logger.info(f"✅ Backup created: {backup_path}")
            return backup_path
        except Exception as e:
            logger.warning(f"⚠️ Failed to backup {target}: {e}")
            return None
    
    def get_latest_backup(self) -> Optional[Path]:
        """Get the most recent backup file path."""
        target = self._target_lessons_path()
        backups_dir = target.parent / "content_backups"
        if not backups_dir.exists():
            return None
        backups = sorted(backups_dir.glob(f"{target.stem}.*{target.suffix}"), reverse=True)
        return backups[0] if backups else None
    
    def get_all_backups(self) -> List[Tuple[Path, datetime]]:
        """Get all backup files with their timestamps."""
        target = self._target_lessons_path()
        backups_dir = target.parent / "content_backups"
        if not backups_dir.exists():
            return []
        backups = []
        for backup_path in backups_dir.glob(f"{target.stem}.*{target.suffix}"):
            try:
                # Extract timestamp from filename: lessons.YYYYMMDD-HHMMSS.json
                parts = backup_path.stem.split(".")
                if len(parts) >= 2:
                    ts_str = parts[-1]
                    ts = datetime.strptime(ts_str, "%Y%m%d-%H%M%S")
                    backups.append((backup_path, ts))
            except Exception:
                # If timestamp parsing fails, use file mtime
                ts = datetime.fromtimestamp(backup_path.stat().st_mtime)
                backups.append((backup_path, ts))
        return sorted(backups, key=lambda x: x[1], reverse=True)
    
    def restore_from_backup(self, backup_path: Optional[Path] = None) -> bool:
        """
        Restore lessons.json from a backup.
        If backup_path is None, uses the latest backup.
        Returns True if successful.
        """
        try:
            if backup_path is None:
                backup_path = self.get_latest_backup()
            if not backup_path or not backup_path.exists():
                logger.error("No backup found to restore from")
                return False
            
            target = self._target_lessons_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            
            # Create a backup of current file before restoring
            if target.exists():
                self._backup_file_if_exists(target)
            
            shutil.copy2(backup_path, target)
            logger.info(f"✅ Restored lessons.json from backup: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to restore from backup: {e}", exc_info=True)
            return False

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
            self._backup_file_if_exists(target)
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
            
            # Process Drive-linked media referenced in the text/task (same as in _sync_from_master_doc)
            # Replace links with markers and download files
            media_markers: Dict[str, Dict[str, Any]] = {}  # marker_id -> media_info
            
            # Find all Drive links in lesson and task text
            combined_text = (lesson_text or "") + "\n" + (task_text or "")
            drive_links = self._find_drive_links_with_positions(combined_text)
            
            logger.info(f"   📎 Day {day}: Found {len(drive_links)} Drive links in text")
            
            # Process links in reverse order to preserve positions when replacing
            drive_links.sort(key=lambda x: x["start"], reverse=True)
            
            processed_links = 0
            skipped_links = 0
            error_links = 0
            
            for link_info in drive_links:
                fid = link_info["file_id"]
                link_url = link_info["url"]
                is_folder = link_info.get("is_folder", False)
                
                try:
                    if is_folder:
                        # Handle folder: get all files in folder and process each as media
                        folder_id = link_info.get("folder_id") or fid
                        logger.info(f"   📁 Processing Drive folder: {link_url[:60]}... (folder_id: {folder_id})")
                        
                        folder_files = self._list_children(drive, folder_id)
                        logger.info(f"   📁   Found {len(folder_files)} items in folder")
                        
                        if not folder_files:
                            logger.warning(f"   ⚠️ Folder {folder_id} is empty or inaccessible")
                            skipped_links += 1
                            continue
                        
                        folder_markers = []
                        for folder_file in folder_files:
                            file_id = folder_file.get("id")
                            file_name = folder_file.get("name", f"file_{file_id}").strip()
                            file_mime = (folder_file.get("mimeType") or "").lower()
                            
                            if file_mime.startswith("image/"):
                                media_type = "photo"
                            elif file_mime.startswith("video/"):
                                media_type = "video"
                            else:
                                continue
                            
                            safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", file_name)
                            dest = media_root / f"day_{day:02d}" / safe_name
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            
                            should_skip = self._should_skip_download(dest, folder_file.get("size"), folder_file.get("modifiedTime"))
                            if not should_skip or not dest.exists():
                                self._download_binary_file(drive, file_id, dest)
                                media_downloaded += 1
                            
                            processed_links += 1
                            rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                            
                            marker_id = f"MEDIA_{file_id}_{len(media_markers)}"
                            media_markers[marker_id] = {
                                "type": media_type,
                                "path": rel_path,
                                "file_id": file_id,
                                "name": file_name
                            }
                            folder_markers.append(marker_id)
                        
                        if folder_markers:
                            markers_text = "\n".join([f"[{m}]" for m in folder_markers])
                            if link_url in lesson_text:
                                lesson_text = lesson_text.replace(link_url, markers_text)
                            if link_url in task_text:
                                task_text = task_text.replace(link_url, markers_text)
                    else:
                        # Handle single file
                        logger.info(f"   📎 Processing Drive link: {link_url[:60]}... (file_id: {fid})")
                        meta = drive.files().get(fileId=fid, fields="id,name,mimeType,modifiedTime,size").execute()
                        mt = (meta.get("mimeType") or "").lower()
                        name = (meta.get("name") or f"file_{fid}").strip()
                        
                        if mt.startswith("image/"):
                            media_type = "photo"
                        elif mt.startswith("video/"):
                            media_type = "video"
                        else:
                            skipped_links += 1
                            continue
                        
                        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
                        dest = media_root / f"day_{day:02d}" / safe_name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        
                        should_skip = self._should_skip_download(dest, meta.get("size"), meta.get("modifiedTime"))
                        if not should_skip or not dest.exists():
                            self._download_binary_file(drive, fid, dest)
                            media_downloaded += 1
                        
                        processed_links += 1
                        rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                        
                        marker_id = f"MEDIA_{fid}_{len(media_markers)}"
                        media_markers[marker_id] = {
                            "type": media_type,
                            "path": rel_path,
                            "file_id": fid,
                            "name": name
                        }
                        
                        marker_placeholder = f"[{marker_id}]"
                        if link_url in lesson_text:
                            lesson_text = lesson_text.replace(link_url, marker_placeholder)
                        if link_url in task_text:
                            task_text = task_text.replace(link_url, marker_placeholder)
                except Exception as e:
                    error_links += 1
                    error_msg = f"day {day}: failed to download linked media ({fid}): {e}"
                    logger.error(f"   ❌ {error_msg}")
                    warnings.append(error_msg)
            
            if drive_links:
                logger.info(f"   📎 Day {day} summary: {processed_links} processed, {skipped_links} skipped, {error_links} errors, {media_downloaded} downloaded")

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
                    if not self._should_skip_download(dest, m.get("size"), m.get("modifiedTime")):
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

            # Split lesson into posts by square brackets
            lesson_posts = DriveContentSync._split_lesson_into_posts((lesson_text or "").strip())
            
            entry: Dict[str, Any] = {
                "day_number": day,
                "title": title,
                "text": lesson_posts if len(lesson_posts) > 1 else (lesson_posts[0] if lesson_posts else ""),
                "task": (task_text or "").strip(),
            }
            if media_items:
                entry["media"] = media_items
            # CRITICAL: Store media markers for inline insertion
            if media_markers:
                entry["media_markers"] = media_markers
                logger.info(f"   ✅ Stored {len(media_markers)} media_markers in entry for day {day}")
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
        self._backup_file_if_exists(target)
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
