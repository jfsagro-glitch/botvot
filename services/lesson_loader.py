"""
Сервис для загрузки уроков из JSON файла.

Загружает структуру уроков из data/lessons.json и предоставляет
интерфейс для доступа к урокам.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from core.models import Lesson, Tariff

logger = logging.getLogger(__name__)


class LessonLoader:
    """Загрузчик уроков из JSON файла."""
    
    def __init__(self, lessons_file: str = None):
        """
        Инициализация загрузчика.
        
        Args:
            lessons_file: Путь к JSON файлу с уроками (если None, используется data/lessons.json)
        """
        # IMPORTANT (Railway Volumes):
        # Many Railway setups mount a Volume to /app/data to persist SQLite.
        # That mount shadows the repository `data/` directory inside the container,
        # so `data/lessons.json` becomes invisible and LessonLoader ends up with 0 lessons.
        # To make deployments robust, we support a "seed" directory outside the mount.
        project_root = Path(__file__).parent.parent
        if lessons_file is None:
            lessons_file = project_root / "data" / "lessons.json"
        self.lessons_file = Path(lessons_file)
        self._lessons_cache: Optional[Dict[str, Any]] = None
        self._load_lessons()
    
    def _load_lessons(self):
        """Загружает уроки из JSON файла."""
        logger.info(f"Загрузка уроков из: {self.lessons_file.absolute()}")
        
        old_cache = self._lessons_cache
        
        if not self.lessons_file.exists():
            logger.error(f"❌ Файл уроков {self.lessons_file.absolute()} не найден!")
            logger.error(f"   Текущая рабочая директория: {Path.cwd()}")
            # Пробуем альтернативные пути
            project_root = Path(__file__).parent.parent
            alternative_paths = [
                Path.cwd() / "data" / "lessons.json",
                Path(__file__).parent.parent / "data" / "lessons.json",
                Path("data/lessons.json"),
                # Seed paths (outside /app/data volume mount)
                project_root / "seed_data" / "lessons.json",
                Path.cwd() / "seed_data" / "lessons.json",
                Path("seed_data/lessons.json"),
            ]
            for alt_path in alternative_paths:
                logger.info(f"   Пробую альтернативный путь: {alt_path.absolute()}")
                if alt_path.exists():
                    logger.info(f"   ✅ Найден по альтернативному пути: {alt_path.absolute()}")
                    self.lessons_file = alt_path
                    # Продолжаем загрузку после исправления пути
                    break
            else:
                logger.error(f"   ❌ Файл не найден ни по одному из путей")
                logger.error(f"   Список файлов в data/: {list((Path(__file__).parent.parent / 'data').glob('*.json')) if (Path(__file__).parent.parent / 'data').exists() else 'директория data не существует'}")
                seed_dir = Path(__file__).parent.parent / "seed_data"
                logger.error(f"   Список файлов в seed_data/: {list(seed_dir.glob('*.json')) if seed_dir.exists() else 'директория seed_data не существует'}")
                # Пробуем также проверить текущую директорию
                cwd_data = Path.cwd() / "data"
                if cwd_data.exists():
                    logger.info(f"   Содержимое {cwd_data}: {list(cwd_data.glob('*.json'))}")
                cwd_seed = Path.cwd() / "seed_data"
                if cwd_seed.exists():
                    logger.info(f"   Содержимое {cwd_seed}: {list(cwd_seed.glob('*.json'))}")
                self._lessons_cache = {}
                return
        
        try:
            with open(self.lessons_file, "r", encoding="utf-8") as f:
                new_cache = json.load(f)

            # Basic validation: must be a dict with numeric day keys
            if not isinstance(new_cache, dict) or not any(k.isdigit() for k in new_cache.keys()):
                logger.error(f"❌ Загруженный файл {self.lessons_file} не содержит корректной структуры уроков; пропускаю перезагрузку")
                # keep old cache if present
                if old_cache is None:
                    self._lessons_cache = {}
                else:
                    self._lessons_cache = old_cache
                return

            # If new cache is empty but we have old cache, keep old one (protect against bad sync)
            if (not new_cache or len([k for k in new_cache.keys() if k.isdigit()]) == 0) and old_cache:
                logger.warning(f"⚠️ Загруженный lessons.json пустой; сохраняю прежний кэш уроков")
                self._lessons_cache = old_cache
                return

            self._lessons_cache = new_cache
            logger.info(f"✅ Загружено {len(self._lessons_cache)} уроков из {self.lessons_file.absolute()}")
            if self._lessons_cache:
                available_days = sorted([int(k) for k in self._lessons_cache.keys() if k.isdigit()])
                logger.info(f"   Доступные дни: {available_days[:20]}...")
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке уроков: {e}", exc_info=True)
            # keep old cache if available
            if old_cache is None:
                self._lessons_cache = {}
            else:
                self._lessons_cache = old_cache
    
    def reload(self):
        """Перезагружает уроки из файла."""
        self._load_lessons()
    
    def get_lesson(self, day: int) -> Optional[Dict[str, Any]]:
        """
        Получает урок по номеру дня.
        
        Args:
            day: Номер дня курса (0-30)
        
        Returns:
            Словарь с данными урока или None
        """
        if not self._lessons_cache:
            logger.warning(f"Lessons cache is empty when trying to get lesson for day {day}")
            return None
        
        day_key = str(day)
        lesson = self._lessons_cache.get(day_key)
        
        if lesson is None:
            logger.warning(f"Lesson not found for day {day} (key: '{day_key}'). Available keys: {sorted([k for k in self._lessons_cache.keys() if k.isdigit()])[:20]}")
        else:
            logger.debug(f"Lesson found for day {day}: {lesson.get('title', 'No title')}")
        
        return lesson
    
    def get_task_for_tariff(self, day: int, tariff: Tariff) -> str:
        """
        Получает задание для урока в зависимости от тарифа.
        
        Args:
            day: Номер дня курса
            tariff: Тариф пользователя
        
        Returns:
            Текст задания (с учетом тарифа: для BASIC убирается текст про обратную связь,
            для FEEDBACK убирается префикс "💡 Для тарифа с обратной связью:")
        """
        lesson = self.get_lesson(day)
        if not lesson:
            return ""
        
        # Для тарифа с обратной связью (FEEDBACK, PREMIUM)
        if tariff in [Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]:
            task = lesson.get("task_feedback") or lesson.get("task", "")
            # Убираем префикс "💡 Для тарифа с обратной связью: " если он есть, оставляя содержимое
            feedback_prefix = "💡 Для тарифа с обратной связью:"
            if feedback_prefix in task:
                # Заменяем префикс и следующий за ним пробел/перенос на пустую строку
                # Сначала заменяем префикс с возможным пробелом после него
                task = task.replace(feedback_prefix + " ", "").replace(feedback_prefix, "")
                # Убираем лишние переносы строк и пробелы в начале строк
                task = task.replace("\n\n\n", "\n\n").strip()
                # Убираем пробелы в начале каждой строки после переноса
                lines = task.split("\n")
                task = "\n".join(line.lstrip() if line.strip() else line for line in lines)
            return task
        else:
            # Для базового тарифа (BASIC)
            # IMPORTANT: базовый тариф тоже сдаёт задания, поэтому:
            # - если task_basic отсутствует, используем общий task или task_feedback
            task = lesson.get("task_basic") or lesson.get("task") or lesson.get("task_feedback") or ""

            # Убираем маркер "Для тарифа с обратной связью", но НЕ выкидываем само задание.
            feedback_prefixes = [
                "💡 Для тарифа с обратной связью:",
                "Для тарифа с обратной связью:",
            ]
            for feedback_prefix in feedback_prefixes:
                if feedback_prefix in task:
                    task = task.replace(feedback_prefix + " ", "").replace(feedback_prefix, "")

            task = task.replace("\n\n\n", "\n\n").strip()
            return task
    
    def is_silent_day(self, day: int) -> bool:
        """Проверяет, является ли день 'днем тишины'."""
        lesson = self.get_lesson(day)
        if not lesson:
            return False
        return lesson.get("silent", False)
    
    def get_all_lessons(self) -> Dict[str, Any]:
        """Возвращает все уроки."""
        return self._lessons_cache or {}
    
    def get_lesson_count(self) -> int:
        """Возвращает количество уроков."""
        return len(self._lessons_cache) if self._lessons_cache else 0
    
    def convert_to_lesson_model(self, day: int) -> Optional[Lesson]:
        """
        Конвертирует данные из JSON в модель Lesson.
        
        Args:
            day: Номер дня курса
        
        Returns:
            Объект Lesson или None
        """
        lesson_data = self.get_lesson(day)
        if not lesson_data:
            return None
        
        # Получаем медиа
        media = lesson_data.get("media", [])
        image_url = None
        video_url = None
        
        for media_item in media:
            if media_item.get("type") == "photo" and not image_url:
                # Для фото используем file_id или путь
                image_url = media_item.get("file_id") or media_item.get("path")
            elif media_item.get("type") == "video" and not video_url:
                video_url = media_item.get("file_id") or media_item.get("path")
        
        # created_at is required by Lesson dataclass in core.models.
        # JSON lessons don't include it, so we stamp "now" deterministically.
        from datetime import datetime
        return Lesson(
            lesson_id=day,
            day_number=day,
            title=lesson_data.get("title", f"День {day}"),
            content_text=lesson_data.get("text", ""),
            image_url=image_url,
            video_url=video_url,
            assignment_text=lesson_data.get("task", ""),
            created_at=datetime.utcnow(),
        )
