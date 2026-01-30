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
    total_blocks: int  # Общее количество блоков (posts) во всех уроках
    total_media_files: int  # Общее количество медиафайлов (обработанных, не только загруженных)
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
        
        # IMPORTANT: do NOT dedupe occurrences here. The same Drive URL can appear multiple times
        # and must be replaced in every position to preserve exact placement. Downloading is
        # deduped later by file_id/folder_id.
        return links

    @staticmethod
    def _clean_text_markers(text: str) -> str:
        """
        Удаляет служебные маркеры [a][b][c][d] из текста урока.
        """
        if not text:
            return text
        # Удаляем [a][b][c][d] в конце текста (может быть с пробелами/переносами строк перед ним)
        # Используем DOTALL чтобы . включал переносы строк, и MULTILINE для ^ и $
        text = re.sub(r'\s*\[a\]\[b\]\[c\]\[d\]\s*$', '', text, flags=re.MULTILINE | re.DOTALL)
        return text
    
    @staticmethod
    def _split_lesson_into_posts(lesson_text: str, max_length: int = 4000) -> List[str]:
        """
        Разделяет текст урока на посты с сохранением форматирования.
        
        Поддерживает:
        1. Ручные маркеры: [POST], [ДОПОЛНЕНИЕ], [BLOCK], [БЛОК] на отдельной строке
        2. Автоматическое разделение по длине (>4000 символов) на границах абзацев
        
        ВАЖНО: Сохраняет все форматирование (пробелы, отступы, пунктуацию, эмодзи)
        ВАЖНО: Медиа-маркеры [MEDIA_...] НЕ являются разделителями постов
        
        Args:
            lesson_text: Полный текст урока
            max_length: Максимальная длина поста (по умолчанию 4000, лимит Telegram 4096)
        
        Returns:
            Список текстов постов
        """
        if not lesson_text or not lesson_text.strip():
            return [""]
        
        # Шаг 1: Разделение по ручным маркерам
        # Поддерживаем: [POST], [ДОПОЛНЕНИЕ], [BLOCK], [БЛОК] на отдельной строке
        # ВАЖНО: Сохраняем все форматирование, включая пробелы и переносы строк
        
        # Разбиваем на строки, сохраняя переносы строк
        lines = lesson_text.split('\n')
        posts = []
        current_post_lines = []
        
        for line in lines:
            line_stripped = line.strip()
            
            # Проверяем, является ли строка медиа-маркером (не разделитель постов)
            is_media_marker = re.match(r'^\s*\[MEDIA_[a-zA-Z0-9_-]+\]\s*$', line)
            
            # Проверяем, является ли строка маркером разделения постов
            # Поддерживаем: [POST], [ДОПОЛНЕНИЕ], [BLOCK], [БЛОК] и их вариации
            is_post_marker = False
            if not is_media_marker:
                # Проверяем маркеры на отдельной строке
                post_marker_pattern = r'^\s*\[(POST|ДОПОЛНЕНИЕ|BLOCK|БЛОК|POST\d*|ДОПОЛНЕНИЕ\d*)\]\s*$'
                if re.match(post_marker_pattern, line, re.IGNORECASE):
                    is_post_marker = True
            
            if is_post_marker:
                # Сохраняем текущий пост, если он не пустой
                if current_post_lines:
                    post_text = '\n'.join(current_post_lines)
                    # Убираем только лишние пробелы в начале и конце, но сохраняем внутреннее форматирование
                    post_text = post_text.rstrip()
                    if post_text:
                        posts.append(post_text)
                    current_post_lines = []
                # Маркер не включается в пост
            else:
                # Добавляем строку в текущий пост (сохраняем форматирование)
                current_post_lines.append(line)
        
        # Добавляем последний пост, если он есть
        if current_post_lines:
            post_text = '\n'.join(current_post_lines)
            post_text = post_text.rstrip()
            if post_text:
                posts.append(post_text)
        
        # Если нашли маркеры и разделили, возвращаем посты
        if len(posts) > 1:
            return posts
        
        # Шаг 2: Если ручных маркеров нет, проверяем длину и разделяем автоматически при необходимости
        lesson_text_stripped = lesson_text.rstrip()
        if len(lesson_text_stripped) <= max_length:
            return [lesson_text_stripped] if lesson_text_stripped else [""]
        
        # Автоматическое разделение по абзацам, стараясь сохранить посты под max_length
        posts = []
        # Разбиваем на абзацы (двойные переносы строк), сохраняя форматирование
        paragraphs = lesson_text.split('\n\n')
        current_post_parts = []
        current_length = 0
        
        for para in paragraphs:
            # Сохраняем оригинальный абзац с форматированием
            para_original = para
            para_stripped = para.rstrip()
            
            if not para_stripped:
                # Пустой абзац - добавляем как есть для сохранения форматирования
                if current_post_parts:
                    current_post_parts.append("")
                continue
            
            para_length = len(para_stripped)
            
            # Если один абзац превышает max_length, разбиваем его по строкам
            if para_length > max_length:
                # Сохраняем текущий пост, если он есть
                if current_post_parts:
                    post_text = '\n\n'.join(current_post_parts)
                    post_text = post_text.rstrip()
                    if post_text:
                        posts.append(post_text)
                    current_post_parts = []
                    current_length = 0
                
                # Разбиваем длинный абзац по строкам
                para_lines = para.split('\n')
                current_line_parts = []
                current_line_length = 0
                
                for line in para_lines:
                    line_stripped = line.rstrip()
                    if not line_stripped:
                        # Пустая строка - добавляем как есть
                        if current_line_parts:
                            current_line_parts.append("")
                        continue
                    
                    line_length = len(line_stripped)
                    # Проверяем, не превысит ли добавление строки max_length
                    if current_line_parts and current_line_length + line_length + 2 > max_length:  # +2 для \n\n
                        # Сохраняем текущий пост
                        if current_line_parts:
                            post_text = '\n'.join(current_line_parts)
                            post_text = post_text.rstrip()
                            if post_text:
                                posts.append(post_text)
                            current_line_parts = []
                            current_line_length = 0
                    
                    current_line_parts.append(line)
                    current_line_length += len(line) + 1  # +1 для \n
                
                # Добавляем оставшиеся строки как последний пост
                if current_line_parts:
                    post_text = '\n'.join(current_line_parts)
                    post_text = post_text.rstrip()
                    if post_text:
                        posts.append(post_text)
            else:
                # Проверяем, не превысит ли добавление абзаца max_length
                if current_post_parts and current_length + para_length + 2 > max_length:  # +2 для \n\n
                    # Сохраняем текущий пост
                    post_text = '\n\n'.join(current_post_parts)
                    post_text = post_text.rstrip()
                    if post_text:
                        posts.append(post_text)
                    current_post_parts = []
                    current_length = 0
                
                # Добавляем абзац в текущий пост (сохраняем оригинальное форматирование)
                current_post_parts.append(para_original)
                current_length += len(para_original) + 2  # +2 для \n\n
        
        # Добавляем оставшийся пост
        if current_post_parts:
            post_text = '\n\n'.join(current_post_parts)
            post_text = post_text.rstrip()
            if post_text:
                posts.append(post_text)
        
        return posts if posts else [lesson_text_stripped] if lesson_text_stripped else [""]

    def _split_master_doc(self, text: str) -> Dict[int, Dict[str, str]]:
        """
        Разделяет мастер-документ (plain text export) на блоки по дням.
        
        Ожидаемые маркеры в документе (русский или английский):
          - "День 0" ... "День 30"
          - "Day 0" ... "Day 30"
        
        Внутри каждого блока дня:
          - Опциональная строка заголовка: "Заголовок: ..." или "Title: ..."
          - Задание начинается со строки, начинающейся с "Задание:" / "Task:"
          - Вводный текст: "Intro:" или "Введение:" или "Вводный текст:"
          - Обо мне: "Обо мне:" или "About me:" или "About_me:"
        
        ВАЖНО: Сохраняет все форматирование (пробелы, отступы, пунктуацию, эмодзи)
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
                    # Если заголовок имеет название после двоеточия, сохраняем его как первую строку
                    title_hint = (m.group(2) or "").strip()
                    if title_hint:
                        blocks[day].append(f"Заголовок: {title_hint}")
                    continue
            if current_day is not None:
                blocks[current_day].append(line)

        out: Dict[int, Dict[str, str]] = {}
        for day, bl in blocks.items():
            # Сохраняем оригинальный текст с форматированием
            raw = "\n".join(bl)
            if not raw.strip():
                continue

            title = ""
            lesson = raw
            task = ""
            intro_text = ""
            about_me_text = ""

            # Извлекаем заголовок
            for ln in bl[:10]:
                mm = re.match(r"^\s*(?:Заголовок|Title)\s*:\s*(.+)\s*$", ln, re.IGNORECASE)
                if mm:
                    title = mm.group(1).strip()
                    break

            # Разделяем на секции: task, intro_text, about_me_text
            # Поддерживаем оба формата:
            #   "Задание:" (на отдельной строке)
            #   "Задание: текст задания" (текст на той же строке)
            task_re = re.compile(r"^\s*(?:Задание|Task)\s*:\s*(.*)$", re.IGNORECASE)
            intro_re = re.compile(r"^\s*(?:Intro|Введение|Вводный текст)\s*:\s*(.*)$", re.IGNORECASE)
            about_me_re = re.compile(r"^\s*(?:Обо мне|About me|About_me)\s*:\s*(.*)$", re.IGNORECASE)
            
            parts_lesson: List[str] = []
            parts_task: List[str] = []
            parts_intro: List[str] = []
            parts_about_me: List[str] = []
            
            in_task = False
            in_intro = False
            in_about_me = False
            
            for ln in bl:
                # Проверяем маркер задания
                m_task = task_re.match(ln)
                if m_task:
                    in_task = True
                    in_intro = False
                    in_about_me = False
                    task_text_on_line = (m_task.group(1) or "").strip()
                    if task_text_on_line:
                        parts_task.append(task_text_on_line)
                    continue
                
                # Проверяем маркер вводного текста
                m_intro = intro_re.match(ln)
                if m_intro:
                    in_intro = True
                    in_task = False
                    in_about_me = False
                    intro_text_on_line = (m_intro.group(1) or "").strip()
                    if intro_text_on_line:
                        parts_intro.append(intro_text_on_line)
                    continue
                
                # Проверяем маркер "Обо мне"
                m_about_me = about_me_re.match(ln)
                if m_about_me:
                    in_about_me = True
                    in_task = False
                    in_intro = False
                    about_me_text_on_line = (m_about_me.group(1) or "").strip()
                    if about_me_text_on_line:
                        parts_about_me.append(about_me_text_on_line)
                    continue
                
                # Добавляем строку в соответствующую секцию
                if in_task:
                    parts_task.append(ln)
                elif in_intro:
                    parts_intro.append(ln)
                elif in_about_me:
                    parts_about_me.append(ln)
                else:
                    parts_lesson.append(ln)
            
            # Собираем текст секций, сохраняя форматирование
            # Убираем только лишние пробелы в начале и конце, но сохраняем внутреннее форматирование
            lesson = "\n".join(parts_lesson).rstrip()
            task = "\n".join(parts_task).rstrip()
            intro_text = "\n".join(parts_intro).rstrip()
            about_me_text = "\n".join(parts_about_me).rstrip()

            # Разделяем урок на посты:
            # 1. По ручным маркерам: [POST], [ДОПОЛНЕНИЕ] и т.д.
            # 2. Автоматически по длине (>4000 символов)
            lesson_posts = DriveContentSync._split_lesson_into_posts(lesson)
            
            # Если урок разделен на несколько постов, сохраняем как список; иначе как строку (обратная совместимость)
            lesson_data = {
                "title": title, 
                "lesson": lesson_posts if len(lesson_posts) > 1 else (lesson_posts[0] if lesson_posts else ""), 
                "task": task
            }
            
            # Сохраняем intro_text и about_me_text, если они были извлечены
            if intro_text:
                lesson_data["intro_text"] = intro_text
            if about_me_text:
                lesson_data["about_me_text"] = about_me_text
            
            out[day] = lesson_data

        return out
    
    def _sync_from_master_doc(self, drive, warnings: List[str]) -> Tuple[Dict[str, Any], int, int, int]:
        master_id = (Config.DRIVE_MASTER_DOC_ID or "").strip()
        if not master_id:
            raise RuntimeError("DRIVE_MASTER_DOC_ID is empty")

        # Скачиваем текст мастер-документа
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
        total_blocks = 0
        total_media_files = 0
        for day, data in sorted(day_map.items(), key=lambda x: x[0]):
            title = (data.get("title") or "").strip() or f"День {day}"
            lesson_raw = data.get("lesson") or ""
            # Поддерживаем много-постовые уроки, возвращаемые как список
            # ВАЖНО: Если урок уже разделен на посты (через маркеры [POST]), сохраняем структуру постов
            lesson_was_split = isinstance(lesson_raw, list)
            if lesson_was_split:
                lesson_posts_list = [str(x).rstrip() for x in lesson_raw if str(x).strip()]
                lesson_text = "\n\n".join(lesson_posts_list)
            else:
                lesson_text = str(lesson_raw).rstrip()
                lesson_posts_list = None
            task_text = (data.get("task") or "").rstrip()
            intro_text = (data.get("intro_text") or "").rstrip()
            about_me_text = (data.get("about_me_text") or "").rstrip()

            # Обрабатываем медиа, связанные с Drive, упомянутые в тексте/задании
            # Заменяем ссылки на маркеры и скачиваем файлы
            media_items: List[Dict[str, Any]] = []
            media_markers: Dict[str, Dict[str, Any]] = {}  # marker_id -> media_info
            
            # Находим все Drive ссылки в lesson, task, intro_text, и about_me_text
            # ВАЖНО: В режиме master doc intro_text и about_me_text извлекаются из самого текста урока
            # Они уже извлечены в _split_master_doc, поэтому используем их здесь
            combined_text = lesson_text + "\n" + task_text
            if intro_text:
                combined_text += "\n" + intro_text
            if about_me_text:
                combined_text += "\n" + about_me_text
            
            drive_links = self._find_drive_links_with_positions(combined_text)
            
            logger.info(f"   📎 Day {day}: Found {len(drive_links)} Drive links in text")
            logger.info(f"   📎 Day {day}: Text lengths - lesson: {len(lesson_text)}, task: {len(task_text)}, intro: {len(intro_text)}, about_me: {len(about_me_text)}")
            if drive_links:
                for idx, link in enumerate(drive_links, 1):
                    logger.info(f"   📎   [{idx}/{len(drive_links)}] Link: {link['url'][:80]}... (file_id: {link['file_id']}, is_folder: {link.get('is_folder', False)}, position: {link.get('start', '?')}-{link.get('end', '?')})")
            else:
                logger.warning(f"   ⚠️ Day {day}: No Drive links found in text! This may indicate a problem with link detection.")
            
            # Обрабатываем ссылки в обратном порядке, чтобы сохранить позиции при замене
            drive_links.sort(key=lambda x: x["start"], reverse=True)
            
            processed_links = 0
            skipped_links = 0
            error_links = 0
            
            # ВАЖНО: Заменяем ссылки в тексте, сохраняя точные позиции
            # Используем обратный порядок, чтобы позиции не сдвигались при замене
            for link_info in drive_links:
                fid = link_info["file_id"]
                link_url = link_info["url"]
                is_folder = link_info.get("is_folder", False)
                
                try:
                    if is_folder:
                        # Обрабатываем папку: получаем все файлы в папке и обрабатываем каждый как медиа
                        folder_id = link_info.get("folder_id") or fid
                        logger.info(f"   📁 Processing Drive folder: {link_url[:60]}... (folder_id: {folder_id})")
                        
                        # Получаем все файлы в папке
                        folder_files = self._list_children(drive, folder_id)
                        logger.info(f"   📁   Found {len(folder_files)} items in folder")
                        
                        if not folder_files:
                            logger.warning(f"   ⚠️ Folder {folder_id} is empty or inaccessible")
                            skipped_links += 1
                            continue
                        
                        # Собираем все маркеры для файлов в этой папке
                        folder_markers = []
                        folder_media_count = 0
                        
                        for folder_file in folder_files:
                            file_id = folder_file.get("id")
                            file_name = folder_file.get("name", f"file_{file_id}").strip()
                            file_mime = (folder_file.get("mimeType") or "").lower()
                            
                            logger.info(f"   📁   Processing folder item: {file_name} (MIME: {file_mime})")
                            
                            # Обрабатываем только медиа-файлы (изображения/видео)
                            if file_mime.startswith("image/"):
                                media_type = "photo"
                            elif file_mime.startswith("video/"):
                                media_type = "video"
                            else:
                                logger.info(f"   📁   Skipping non-media file in folder: {file_name} (MIME: {file_mime})")
                                continue
                            
                            safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", file_name)
                            dest = media_root / f"day_{day:02d}" / safe_name
                            
                            # Убеждаемся, что директория назначения существует
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
                            
                            processed_links += 1
                            rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                            
                            # КРИТИЧЕСКИ ВАЖНО: Проверяем, не был ли уже создан маркер для этого file_id
                            # Если один и тот же файл встречается в папке несколько раз, используем один маркер
                            marker_id = None
                            for existing_marker_id, existing_info in media_markers.items():
                                if existing_info.get("file_id") == file_id and existing_info.get("path") == rel_path:
                                    marker_id = existing_marker_id
                                    logger.info(f"   ♻️ Reusing existing marker: [{marker_id}] for file {file_name} (file_id: {file_id})")
                                    break
                            
                            # Если маркер не найден, создаем новый
                            if not marker_id:
                                marker_id = f"MEDIA_{file_id}_{len(media_markers)}"
                                media_markers[marker_id] = {
                                    "type": media_type,
                                    "path": rel_path,
                                    "file_id": file_id,
                                    "name": file_name
                                }
                                logger.info(f"   ✅ Created new media marker: [{marker_id}] for file {file_name} (path: {rel_path})")
                            
                            folder_markers.append(marker_id)
                            folder_media_count += 1
                            
                            media_items.append({"type": media_type, "path": rel_path, "marker_id": marker_id})
                        
                        # Заменяем ссылку на папку всеми маркерами (по одному на строку или через запятую)
                        if folder_markers:
                            # Заменяем URL папки всеми маркерами, по одному на строку
                            markers_text = "\n".join([f"[{m}]" for m in folder_markers])
                            replaced_in_lesson = False
                            replaced_in_task = False
                            replaced_in_intro = False
                            replaced_in_about_me = False
                            
                            # ВАЖНО: Заменяем точную ссылку в каждом поле текста
                            if link_url in lesson_text:
                                lesson_text = lesson_text.replace(link_url, markers_text)
                                replaced_in_lesson = True
                                logger.info(f"   ✅ Replaced Drive folder link in lesson_text with {len(folder_markers)} markers")
                            if link_url in task_text:
                                task_text = task_text.replace(link_url, markers_text)
                                replaced_in_task = True
                                logger.info(f"   ✅ Replaced Drive folder link in task_text with {len(folder_markers)} markers")
                            if intro_text and link_url in intro_text:
                                intro_text = intro_text.replace(link_url, markers_text)
                                replaced_in_intro = True
                                logger.info(f"   ✅ Replaced Drive folder link in intro_text with {len(folder_markers)} markers")
                            if about_me_text and link_url in about_me_text:
                                about_me_text = about_me_text.replace(link_url, markers_text)
                                replaced_in_about_me = True
                                logger.info(f"   ✅ Replaced Drive folder link in about_me_text with {len(folder_markers)} markers")
                            
                            if not replaced_in_lesson and not replaced_in_task and not replaced_in_intro and not replaced_in_about_me:
                                logger.warning(f"   ⚠️ Drive folder link not found in any text field: {link_url[:60]}...")
                            
                            logger.info(f"   📁 Folder processed: {folder_media_count} media files, {len(folder_markers)} markers created")
                        else:
                            logger.warning(f"   ⚠️ No media files found in folder {folder_id}")
                            skipped_links += 1
                    else:
                        # Обрабатываем одиночный файл (существующая логика)
                        logger.info(f"   📎 Processing Drive link: {link_url[:60]}... (file_id: {fid})")
                        
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
                            continue  # Пропускаем не-медиа файлы
                        
                        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
                        dest = media_root / f"day_{day:02d}" / safe_name
                        
                        # Убеждаемся, что директория назначения существует
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
                            # Считаем существующие файлы как "обработанные" для отчетности
                            skipped_links += 1
                        
                        processed_links += 1
                        rel_path = str(dest.relative_to(project_root)).replace("\\", "/")
                        
                        # КРИТИЧЕСКИ ВАЖНО: Проверяем, не был ли уже создан маркер для этого file_id
                        # Если один и тот же файл встречается в тексте несколько раз, используем один маркер
                        marker_id = None
                        for existing_marker_id, existing_info in media_markers.items():
                            if existing_info.get("file_id") == fid and existing_info.get("path") == rel_path:
                                marker_id = existing_marker_id
                                logger.info(f"   ♻️ Reusing existing marker: [{marker_id}] for file {name} (file_id: {fid})")
                                break
                        
                        # Если маркер не найден, создаем новый
                        if not marker_id:
                            marker_id = f"MEDIA_{fid}_{len(media_markers)}"
                            media_markers[marker_id] = {
                                "type": media_type,
                                "path": rel_path,
                                "file_id": fid,
                                "name": name
                            }
                            logger.info(f"   ✅ Created new media marker: [{marker_id}] for file {name} (path: {rel_path})")
                        
                        # Заменяем ссылку в тексте на маркер
                        # Используем оригинальный URL из текста (link_url) для точной замены
                        marker_placeholder = f"[{marker_id}]"
                        # Заменяем все вхождения ссылки URL в обоих текстах, intro_text, и about_me_text
                        replaced_in_lesson = False
                        replaced_in_task = False
                        replaced_in_intro = False
                        replaced_in_about_me = False
                        
                        # ВАЖНО: Заменяем точную ссылку в каждом поле текста
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

            # Разделяем урок на посты по квадратным скобкам (если еще не разделен)
            # ВАЖНО: Если урок уже был разделен на посты в _split_master_doc, 
            # нужно разделить его снова после замены ссылок на маркеры,
            # чтобы маркеры медиа остались в правильных постах
            lesson_posts = DriveContentSync._split_lesson_into_posts(lesson_text)
            
            # Логируем информацию о блоках для диагностики
            logger.info(f"   📦 Day {day}: Split into {len(lesson_posts)} blocks")
            for i, post in enumerate(lesson_posts, 1):
                post_preview = post[:100].replace('\n', ' ') if post else "(empty)"
                logger.debug(f"   📦   Block {i}/{len(lesson_posts)}: {len(post)} chars, preview: {post_preview}...")
            
            # Если урок был разделен на посты изначально, убеждаемся, что структура сохранена
            if lesson_was_split and lesson_posts_list:
                # Проверяем, что количество постов совпадает (или близко)
                # Это гарантирует, что маркеры [POST] правильно обработаны
                if len(lesson_posts) < len(lesson_posts_list):
                    logger.warning(f"   ⚠️ Day {day}: Number of posts changed after link replacement ({len(lesson_posts_list)} -> {len(lesson_posts)}). This may indicate a problem with [POST] marker processing.")
                elif len(lesson_posts) > len(lesson_posts_list):
                    logger.info(f"   📎 Day {day}: Number of posts increased after link replacement ({len(lesson_posts_list)} -> {len(lesson_posts)}). This is normal if new [POST] markers were added.")
            
            # Валидация блоков: проверяем, что нет пустых блоков
            valid_lesson_posts = [post for post in lesson_posts if post and post.strip()]
            if len(valid_lesson_posts) != len(lesson_posts):
                empty_count = len(lesson_posts) - len(valid_lesson_posts)
                logger.warning(f"   ⚠️ Day {day}: Found {empty_count} empty blocks, removing them")
                lesson_posts = valid_lesson_posts
            
            # Проверяем, что весь текст сохраняется
            original_length = len(lesson_text)
            saved_length = sum(len(post) for post in lesson_posts)
            if saved_length < original_length * 0.95:  # Допускаем небольшую потерю при rstrip()
                logger.warning(f"   ⚠️ Day {day}: Possible text loss detected! Original: {original_length} chars, Saved: {saved_length} chars")
            else:
                logger.debug(f"   ✅ Day {day}: Text integrity check passed (original: {original_length}, saved: {saved_length} chars)")
            
            # Сохраняем текст: список блоков, если больше одного, иначе строка
            text_to_save = lesson_posts if len(lesson_posts) > 1 else (lesson_posts[0] if lesson_posts else "")
            
            # Удаляем служебные маркеры [a][b][c][d] из текста
            if isinstance(text_to_save, list):
                text_to_save = [DriveContentSync._clean_text_markers(post) for post in text_to_save]
                logger.info(f"   ✅ Day {day}: Saving {len(text_to_save)} blocks as list (total {saved_length} chars)")
            else:
                text_to_save = DriveContentSync._clean_text_markers(text_to_save)
                logger.info(f"   ✅ Day {day}: Saving single block as string ({len(text_to_save) if text_to_save else 0} chars)")
            
            entry: Dict[str, Any] = {
                "day_number": day,
                "title": title,
                "text": text_to_save,
                "task": task_text,
            }
            
            # Сохраняем intro_text и about_me_text, если они были извлечены из мастер-документа
            if intro_text:
                entry["intro_text"] = intro_text
            if about_me_text:
                entry["about_me_text"] = about_me_text
            
            if media_items:
                entry["media"] = media_items
            # КРИТИЧЕСКИ ВАЖНО: Сохраняем маркеры медиа для встроенной вставки
            # Всегда сохраняем маркеры, если они существуют, даже если файлы не были скачаны
            if media_markers:
                entry["media_markers"] = media_markers
                logger.info(f"   ✅ Stored {len(media_markers)} media_markers in entry for day {day}")
                for marker_id in media_markers.keys():
                    logger.info(f"   📎     - {marker_id}")
            else:
                logger.warning(f"   ⚠️ No media_markers for day {day} (drive_links found: {len(drive_links)})")
            compiled[str(day)] = entry
            
            # Собираем статистику
            total_blocks += len(lesson_posts)
            total_media_files += len(media_markers)

        return compiled, media_downloaded, total_blocks, total_media_files

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
        
        ВАЖНО: Сохраняет все форматирование (пробелы, отступы, пунктуацию, эмодзи)
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
                # Сохраняем данные как есть (включая пробелы, отступы, эмодзи)
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

    def clean_media_files(self) -> int:
        """
        Удаляет все медиафайлы из директории content_media.
        Возвращает количество удаленных файлов.
        """
        project_root = Path.cwd()
        media_root = (project_root / self.media_dir).resolve()
        
        if not media_root.exists():
            logger.info(f"📁 Media directory does not exist: {media_root}")
            return 0
        
        deleted_count = 0
        try:
            # Сначала собираем все файлы для удаления (чтобы избежать проблем с изменением структуры во время итерации)
            files_to_delete = []
            dirs_to_delete = []
            
            for item in media_root.iterdir():
                if item.is_file():
                    files_to_delete.append(item)
                elif item.is_dir():
                    # Собираем все файлы из поддиректорий
                    for subitem in item.rglob("*"):
                        if subitem.is_file():
                            files_to_delete.append(subitem)
                    dirs_to_delete.append(item)
            
            # Удаляем все файлы
            for file_path in files_to_delete:
                try:
                    file_path.unlink()
                    deleted_count += 1
                    logger.debug(f"   🗑️ Deleted file: {file_path.relative_to(media_root)}")
                except Exception as e:
                    logger.warning(f"   ⚠️ Could not delete file {file_path}: {e}")
            
            # Удаляем все директории (shutil.rmtree удаляет рекурсивно, включая все файлы)
            for dir_path in dirs_to_delete:
                try:
                    shutil.rmtree(dir_path)
                    logger.debug(f"   🗑️ Deleted directory: {dir_path.relative_to(media_root)}")
                except Exception as e:
                    logger.warning(f"   ⚠️ Could not delete directory {dir_path}: {e}")
                    # Пробуем удалить оставшиеся файлы вручную
                    for remaining in dir_path.rglob("*"):
                        if remaining.is_file():
                            try:
                                remaining.unlink()
                                deleted_count += 1
                            except Exception:
                                pass
                    # Пробуем удалить директорию снова
                    try:
                        dir_path.rmdir()
                    except Exception:
                        pass
            
            logger.info(f"✅ Cleaned {deleted_count} media files from {media_root}")
            return deleted_count
        except Exception as e:
            logger.error(f"❌ Error cleaning media files: {e}", exc_info=True)
            raise

    def sync_now(self, clean_media: bool = False) -> SyncResult:
        ok, reason = self._admin_ready()
        if not ok:
            raise RuntimeError(f"Drive content sync not ready: {reason}")

        # Очищаем медиафайлы перед синхронизацией, если запрошено
        if clean_media:
            logger.info("🧹 Cleaning media files before sync...")
            deleted_count = self.clean_media_files()
            logger.info(f"✅ Cleaned {deleted_count} media files")

        drive = self._build_drive_client()
        warnings: List[str] = []

        # Single-doc mode
        if (Config.DRIVE_MASTER_DOC_ID or "").strip():
            compiled, media_downloaded, total_blocks, total_media_files = self._sync_from_master_doc(drive, warnings)
            
            # Basic validation: ensure each lesson has text
            for k, v in compiled.items():
                text = v.get("text", "")
                if isinstance(text, list):
                    if not text or all(not (block or "").strip() for block in text):
                        warnings.append(f"day {k}: empty lesson text (all blocks are empty)")
                elif not (text or "").strip():
                    warnings.append(f"day {k}: empty lesson text")
            
            target = self._target_lessons_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            self._backup_file_if_exists(target)
            tmp = target.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(compiled, f, ensure_ascii=False, indent=2)
            os.replace(tmp, target)
            
            # Проверяем, что все блоки сохранены корректно
            total_saved_blocks = 0
            total_saved_chars = 0
            empty_blocks_found = 0
            for day_key, entry in compiled.items():
                text = entry.get("text", "")
                if isinstance(text, list):
                    total_saved_blocks += len(text)
                    for block in text:
                        if block and block.strip():
                            total_saved_chars += len(block)
                        else:
                            empty_blocks_found += 1
                elif text:
                    total_saved_blocks += 1
                    if text.strip():
                        total_saved_chars += len(text)
                    else:
                        empty_blocks_found += 1
            
            logger.info(f"✅ Drive master-doc sync wrote {len(compiled)} lessons to {target}")
            logger.info(f"   📦 Total blocks saved: {total_saved_blocks} (expected: {total_blocks})")
            logger.info(f"   📝 Total characters saved: {total_saved_chars}")
            if total_saved_blocks != total_blocks:
                logger.warning(f"   ⚠️ Block count mismatch! Saved: {total_saved_blocks}, Expected: {total_blocks}")
            if empty_blocks_found > 0:
                logger.warning(f"   ⚠️ Found {empty_blocks_found} empty blocks in saved data!")
            return SyncResult(
                days_synced=len(compiled),
                lessons_path=str(target),
                media_files_downloaded=media_downloaded,
                total_blocks=total_blocks,
                total_media_files=total_media_files,
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
        total_blocks = 0
        total_media_files = 0

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
            logger.info(f"   📎 Day {day}: Text lengths - lesson: {len(lesson_text or '')}, task: {len(task_text or '')}")
            if drive_links:
                for idx, link in enumerate(drive_links, 1):
                    logger.info(f"   📎   [{idx}/{len(drive_links)}] Link: {link['url'][:80]}... (file_id: {link['file_id']}, is_folder: {link.get('is_folder', False)}, position: {link.get('start', '?')}-{link.get('end', '?')})")
            else:
                logger.warning(f"   ⚠️ Day {day}: No Drive links found in text! This may indicate a problem with link detection.")
            
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
                        
                        # КРИТИЧЕСКИ ВАЖНО: Проверяем, не был ли уже создан маркер для этого file_id
                        marker_id = None
                        for existing_marker_id, existing_info in media_markers.items():
                            if existing_info.get("file_id") == file_id and existing_info.get("path") == rel_path:
                                marker_id = existing_marker_id
                                logger.info(f"   ♻️ Reusing existing marker: [{marker_id}] for file {file_name} (file_id: {file_id})")
                                break
                        
                        # Если маркер не найден, создаем новый
                        if not marker_id:
                            marker_id = f"MEDIA_{file_id}_{len(media_markers)}"
                            media_markers[marker_id] = {
                                "type": media_type,
                                "path": rel_path,
                                "file_id": file_id,
                                "name": file_name
                            }
                            logger.info(f"   ✅ Created new media marker: [{marker_id}] for file {file_name} (path: {rel_path})")
                        
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
                        
                        # КРИТИЧЕСКИ ВАЖНО: Проверяем, не был ли уже создан маркер для этого file_id
                        marker_id = None
                        for existing_marker_id, existing_info in media_markers.items():
                            if existing_info.get("file_id") == fid and existing_info.get("path") == rel_path:
                                marker_id = existing_marker_id
                                logger.info(f"   ♻️ Reusing existing marker: [{marker_id}] for file {name} (file_id: {fid})")
                                break
                        
                        # Если маркер не найден, создаем новый
                        if not marker_id:
                            marker_id = f"MEDIA_{fid}_{len(media_markers)}"
                            media_markers[marker_id] = {
                                "type": media_type,
                                "path": rel_path,
                                "file_id": fid,
                                "name": name
                            }
                            logger.info(f"   ✅ Created new media marker: [{marker_id}] for file {name} (path: {rel_path})")
                        
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
            logger.info(f"   📎 Day {day} media_markers created: {len(media_markers)}")
            if media_markers:
                for marker_id, marker_info in media_markers.items():
                    logger.info(f"   📎   - {marker_id}: {marker_info.get('type')}, path={marker_info.get('path')}, name={marker_info.get('name')}")
            elif drive_links:
                logger.warning(f"   ⚠️ Day {day}: Found {len(drive_links)} drive_links but created 0 media_markers! This may indicate a problem.")

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
            lesson_text_clean = (lesson_text or "").rstrip()
            lesson_posts = DriveContentSync._split_lesson_into_posts(lesson_text_clean)
            
            # Логируем информацию о блоках для диагностики
            logger.info(f"   📦 Day {day}: Split into {len(lesson_posts)} blocks")
            for i, post in enumerate(lesson_posts, 1):
                post_preview = post[:100].replace('\n', ' ') if post else "(empty)"
                logger.debug(f"   📦   Block {i}/{len(lesson_posts)}: {len(post)} chars, preview: {post_preview}...")
            
            # Валидация блоков: проверяем, что нет пустых блоков
            valid_lesson_posts = [post for post in lesson_posts if post and post.strip()]
            if len(valid_lesson_posts) != len(lesson_posts):
                empty_count = len(lesson_posts) - len(valid_lesson_posts)
                logger.warning(f"   ⚠️ Day {day}: Found {empty_count} empty blocks, removing them")
                lesson_posts = valid_lesson_posts
            
            # Проверяем, что весь текст сохраняется
            original_length = len(lesson_text_clean)
            saved_length = sum(len(post) for post in lesson_posts)
            if saved_length < original_length * 0.95:  # Допускаем небольшую потерю при rstrip()
                logger.warning(f"   ⚠️ Day {day}: Possible text loss detected! Original: {original_length} chars, Saved: {saved_length} chars")
            else:
                logger.debug(f"   ✅ Day {day}: Text integrity check passed (original: {original_length}, saved: {saved_length} chars)")
            
            # Сохраняем текст: список блоков, если больше одного, иначе строка
            text_to_save = lesson_posts if len(lesson_posts) > 1 else (lesson_posts[0] if lesson_posts else "")
            
            # Удаляем служебные маркеры [a][b][c][d] из текста
            if isinstance(text_to_save, list):
                text_to_save = [DriveContentSync._clean_text_markers(post) for post in text_to_save]
                logger.info(f"   ✅ Day {day}: Saving {len(text_to_save)} blocks as list (total {saved_length} chars)")
            else:
                text_to_save = DriveContentSync._clean_text_markers(text_to_save)
                logger.info(f"   ✅ Day {day}: Saving single block as string ({len(text_to_save) if text_to_save else 0} chars)")
            
            entry: Dict[str, Any] = {
                "day_number": day,
                "title": title,
                "text": text_to_save,
                "task": (task_text or "").rstrip(),
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
            
            # Собираем статистику
            total_blocks += len(lesson_posts)
            total_media_files += len(media_markers)

        if not compiled:
            raise RuntimeError("No lessons compiled (check Drive folder contents)")

        # Basic validation: ensure each lesson has text
        empty_days = 0
        for k, v in compiled.items():
            text = v.get("text", "")
            if isinstance(text, list):
                if not text or all(not (block or "").strip() for block in text):
                    warnings.append(f"day {k}: empty lesson text (all blocks are empty)")
                    empty_days += 1
            elif not (text or "").strip():
                warnings.append(f"day {k}: empty lesson text")
                empty_days += 1

        # If too many days are empty, abort to avoid replacing a good lessons.json with a broken one
        try:
            total_days = len(compiled)
            if total_days > 0 and (empty_days / total_days) > 0.5:
                raise RuntimeError(f"Aborting sync: {empty_days}/{total_days} lessons are empty (safety threshold exceeded)")
        except Exception as e:
            logger.error(f"❌ Drive sync validation failed: {e}")
            raise

        target = self._target_lessons_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._backup_file_if_exists(target)
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(compiled, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)

        # Проверяем, что все блоки сохранены корректно
        total_saved_blocks = 0
        total_saved_chars = 0
        empty_blocks_found = 0
        for day_key, entry in compiled.items():
            text = entry.get("text", "")
            if isinstance(text, list):
                total_saved_blocks += len(text)
                for block in text:
                    if block and block.strip():
                        total_saved_chars += len(block)
                    else:
                        empty_blocks_found += 1
            elif text:
                total_saved_blocks += 1
                if text.strip():
                    total_saved_chars += len(text)
                else:
                    empty_blocks_found += 1
        
        logger.info(f"✅ Drive sync wrote {len(compiled)} lessons to {target}")
        logger.info(f"   📦 Total blocks saved: {total_saved_blocks} (expected: {total_blocks})")
        logger.info(f"   📝 Total characters saved: {total_saved_chars}")
        if total_saved_blocks != total_blocks:
            logger.warning(f"   ⚠️ Block count mismatch! Saved: {total_saved_blocks}, Expected: {total_blocks}")
        if empty_blocks_found > 0:
            logger.warning(f"   ⚠️ Found {empty_blocks_found} empty blocks in saved data!")
        return SyncResult(
            days_synced=len(compiled),
            lessons_path=str(target),
            media_files_downloaded=media_downloaded,
            total_blocks=total_blocks,
            total_media_files=total_media_files,
            warnings=warnings,
        )
