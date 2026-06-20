"""
Загрузка переверстанных (квадратных) изображений в Telegram и обновление file_id.

Контекст: часть изображений (021 Карточки/*.jpg, а также отдельные обложки уроков
2,3,4,8,9,10,12,14,19,20,25 в Photo/video_pic_optimized/) были переверстаны в квадратный
формат с блюр-подложкой. Байты файлов изменились, поэтому старые file_id (привязанные
к старым байтам в Telegram) для НОВЫХ файлов не подходят — старые file_id были временно
оставлены в lessons.json как заглушка, чтобы медиа не пропадало из бота.

Этот скрипт нужно запустить на машине с доступом к api.telegram.org (не в песочнице):
он заново загружает каждое переверстанное изображение в Telegram (в админский чат),
забирает свежий file_id (самый большой размер — message.photo[-1]) и записывает его
обратно в data/lessons.json и seed_data/lessons.json (в оба файла, если путь там есть).

Стиль и логика повторов взяты из scripts/../compress_and_upload_lesson1_video.py
(см. upload_video_with_retry) и core/config.py (Config.COURSE_BOT_TOKEN / ADMIN_CHAT_ID).
"""

import sys
import asyncio
from pathlib import Path
import json

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from core.config import Config
from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

LESSONS_JSON_PATHS = [
    project_root / "data" / "lessons.json",
    project_root / "seed_data" / "lessons.json",
]

# Только эти каталоги считаются "переверстанными" (квадратный формат) для этой задачи.
REFORMATTED_DIR_MARKERS = (
    "video_pic/021 Карточки",
    "video_pic_optimized",
)


def collect_reformatted_targets() -> list:
    """
    Собирает список (day, path) для:
      - day "21" -> cards[*].path  (карточки, 18 файлов)
      - дни 2,3,4,8,9,10,12,14,19,20,25 -> media[*].path (обложки уроков)

    Список читается динамически из data/lessons.json, а не хардкодится,
    чтобы не разойтись с реальными данными (некоторые дни содержат
    несколько media-файлов, например день 9 и день 20).
    """
    targets = []
    seen_paths = set()

    main_json = project_root / "data" / "lessons.json"
    with open(main_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    single_media_days = ["2", "3", "4", "8", "9", "10", "12", "14", "19", "20", "25"]

    for day in single_media_days:
        entry = data.get(day, {})
        for item in entry.get("media", []) or []:
            path = item.get("path", "")
            if path and "video_pic_optimized" in path and path not in seen_paths:
                targets.append((day, path))
                seen_paths.add(path)

    day21 = data.get("21", {})
    for card in day21.get("cards", []) or []:
        path = card.get("path", "")
        if path and "021 Карточки" in path and path not in seen_paths:
            targets.append(("21", path))
            seen_paths.add(path)

    return targets


async def upload_photo_with_retry(bot: Bot, chat_id: int, photo_path: Path,
                                   caption: str = None, max_retries: int = 3) -> str:
    """Загружает фото с повторными попытками. Возвращает file_id наибольшего размера."""
    for attempt in range(max_retries):
        try:
            print(f"   📤 Попытка {attempt + 1}/{max_retries}...")
            photo_file = FSInputFile(photo_path)

            request_timeout = 120 if attempt == 0 else 300

            message = await bot.send_photo(
                chat_id=chat_id,
                photo=photo_file,
                caption=caption,
                request_timeout=request_timeout,
            )

            if message.photo:
                # Telegram возвращает несколько размеров; последний элемент — самый большой.
                return message.photo[-1].file_id
            else:
                raise ValueError("Не удалось получить photo из сообщения")

        except Exception as e:
            if attempt < max_retries - 1:
                delay = (attempt + 1) * 10
                print(f"   ⚠️ Ошибка (попытка {attempt + 1}): {str(e)[:150]}")
                print(f"   🔄 Повтор через {delay} секунд...")
                await asyncio.sleep(delay)
            else:
                print(f"   ❌ Не удалось загрузить после {max_retries} попыток: {e}")
                raise


def update_file_id_everywhere(day: str, path: str, new_file_id: str) -> int:
    """
    Обновляет file_id для записи с данным day+path в обоих lessons.json
    (data/ и seed_data/), если запись там есть. Возвращает число обновлённых файлов.
    """
    updated_files = 0

    for json_path in LESSONS_JSON_PATHS:
        if not json_path.exists():
            continue

        with open(json_path, "r", encoding="utf-8") as f:
            lessons_data = json.load(f)

        entry = lessons_data.get(day)
        if not entry:
            continue

        changed = False

        for item in entry.get("media", []) or []:
            if item.get("path") == path:
                item["file_id"] = new_file_id
                changed = True

        for item in entry.get("cards", []) or []:
            if item.get("path") == path:
                item["file_id"] = new_file_id
                changed = True

        if changed:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(lessons_data, f, ensure_ascii=False, indent=2)
            updated_files += 1
            print(f"   ✅ Обновлён file_id в {json_path.relative_to(project_root)}")

    return updated_files


async def main():
    """Основная функция."""
    print("=" * 70)
    print("📤 ЗАГРУЗКА ПЕРЕВЕРСТАННЫХ (КВАДРАТНЫХ) ИЗОБРАЖЕНИЙ В TELEGRAM")
    print("=" * 70)

    targets = collect_reformatted_targets()
    print(f"\nНайдено файлов для загрузки: {len(targets)}")

    if not targets:
        print("⚠️ Не нашлось ни одного пути для загрузки. Проверьте data/lessons.json.")
        return False

    bot = Bot(
        token=Config.COURSE_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    admin_chat_id = Config.ADMIN_CHAT_ID
    print(f"Чат для загрузки: {admin_chat_id}\n")

    success_count = 0
    failed = []

    try:
        for idx, (day, rel_path) in enumerate(targets, start=1):
            full_path = project_root / rel_path
            print(f"[{idx}/{len(targets)}] День {day}: {rel_path}")

            if not full_path.exists():
                print(f"   ❌ Файл не найден на диске: {full_path}")
                failed.append((day, rel_path, "файл не найден"))
                continue

            try:
                file_id = await upload_photo_with_retry(
                    bot,
                    admin_chat_id,
                    full_path,
                    caption=f"🖼️ День {day}: {full_path.name}",
                )
                print(f"   📋 file_id: {file_id}")

                updated = update_file_id_everywhere(day, rel_path, file_id)
                if updated == 0:
                    print(f"   ⚠️ Не найдена соответствующая запись day={day} path={rel_path} ни в одном lessons.json")
                    failed.append((day, rel_path, "запись не найдена в lessons.json"))
                else:
                    success_count += 1

            except Exception as e:
                print(f"   ❌ Ошибка загрузки: {e}")
                failed.append((day, rel_path, str(e)[:200]))

            # Небольшая пауза между отправками, чтобы не попасть в rate limit Telegram.
            await asyncio.sleep(0.3)

        print("\n" + "=" * 70)
        print(f"✅ Успешно обновлено: {success_count}/{len(targets)}")
        if failed:
            print(f"⚠️ Проблемные файлы ({len(failed)}):")
            for day, rel_path, reason in failed:
                print(f"   - День {day}: {rel_path} -> {reason}")
        print("=" * 70)

        return len(failed) == 0

    finally:
        await bot.session.close()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
