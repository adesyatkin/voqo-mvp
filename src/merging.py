import os
import re
import sys
import logging
from pathlib import Path
from typing import List, Dict, Tuple
from datetime import datetime
from collections import defaultdict

class TranscriptionMerger:
    def __init__(self, script_path: str = None):
        if script_path:
            self.script_dir = Path(script_path).parent
        else:
            self.script_dir = Path(__file__).parent

        self.logger = self._setup_logger()

        # Папки транскрибации (теперь gemma вместо parakeet)
        self.transcription_folders = [
            "transcription_whisper",
            "transcription_gemma",      # заменено
            "transcription_canary"
        ]

        self.logger.info(f"Рабочая директория: {self.script_dir}")

    def _setup_logger(self):
        log_dir = self.script_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"merging_{timestamp}.log"

        logger = logging.getLogger('TranscriptionMerger')
        logger.setLevel(logging.INFO)
        logger.handlers.clear()

        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        logger.info(f"=" * 60)
        logger.info("ИНИЦИАЛИЗАЦИЯ МОДУЛЯ ОБЪЕДИНЕНИЯ ТРАНСКРИПЦИЙ")
        logger.info(f"Лог-файл: {log_file}")
        logger.info(f"=" * 60)
        return logger

    # ... (остальные методы без изменений, за исключением списка папок выше)
    # Для краткости остальной код не повторяю, он должен остаться таким же, как в оригинале.
    # Ниже приведён только метод process_all_folders для наглядности.

    def process_all_folders(self):
        self.logger.info("Проверка наличия папок транскрибации...")
        existing_folders = []
        missing_folders = []
        for folder_name in self.transcription_folders:
            folder_path = self.script_dir / folder_name
            if folder_path.exists() and folder_path.is_dir():
                existing_folders.append(folder_name)
            else:
                missing_folders.append(folder_name)

        if existing_folders:
            self.logger.info(f"Найдены папки транскрибации: {', '.join(existing_folders)}")
        if missing_folders:
            self.logger.warning(f"Отсутствуют папки: {', '.join(missing_folders)}")

        if not existing_folders:
            self.logger.error("Не найдено ни одной папки транскрибации!")
            return

        processed_folders = 0
        for folder_name in self.transcription_folders:
            folder_path = self.script_dir / folder_name
            if not folder_path.exists() or not folder_path.is_dir():
                continue
            self.merge_transcriptions(folder_path)
            processed_folders += 1
            self.logger.info("-" * 50)

        self.logger.info("=" * 50)
        self.logger.info(f"Обработка завершена! Обработано папок: {processed_folders}")
        self.logger.info("=" * 50)

    # Все остальные методы (parse_filename, merge_transcriptions и т.д.) остаются без изменений.