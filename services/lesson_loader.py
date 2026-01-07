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
    
    def __init__(self, lessons_file: str = "data/lessons.json"):
        """
        Инициализация загрузчика.
        
        Args:
            lessons_file: Путь к JSON файлу с уроками
        """
        self.lessons_file = Path(lessons_file)
        self._lessons_cache: Optional[Dict[str, Any]] = None
        self._load_lessons()
    
    def _load_lessons(self):
        """Загружает уроки из JSON файла."""
        if not self.lessons_file.exists():
            logger.warning(f"Файл уроков {self.lessons_file} не найден. Создайте его через scripts/parse_channel.py")
            self._lessons_cache = {}
            return
        
        try:
            with open(self.lessons_file, "r", encoding="utf-8") as f:
                self._lessons_cache = json.load(f)
            logger.info(f"✅ Загружено {len(self._lessons_cache)} уроков из {self.lessons_file}")
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке уроков: {e}", exc_info=True)
            self._lessons_cache = {}
    
    def reload(self):
        """Перезагружает уроки из файла."""
        self._load_lessons()
    
    def get_lesson(self, day: int) -> Optional[Dict[str, Any]]:
        """
        Получает урок по номеру дня.
        
        Args:
            day: Номер дня курса (1-30)
        
        Returns:
            Словарь с данными урока или None
        """
        if not self._lessons_cache:
            return None
        
        return self._lessons_cache.get(str(day))
    
    def get_task_for_tariff(self, day: int, tariff: Tariff) -> str:
        """
        Получает задание для урока в зависимости от тарифа.
        
        Args:
            day: Номер дня курса
            tariff: Тариф пользователя
        
        Returns:
            Текст задания
        """
        lesson = self.get_lesson(day)
        if not lesson:
            return ""
        
        # Приоритет: task_feedback -> task_basic -> task
        if tariff in [Tariff.FEEDBACK, Tariff.PREMIUM]:
            return lesson.get("task_feedback") or lesson.get("task", "")
        else:
            return lesson.get("task_basic") or lesson.get("task", "")
    
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
        
        return Lesson(
            lesson_id=f"lesson_{day}",
            day_number=day,
            title=lesson_data.get("title", f"День {day}"),
            content_text=lesson_data.get("text", ""),
            image_url=image_url,
            video_url=video_url,
            assignment_text=lesson_data.get("task", "")
        )

