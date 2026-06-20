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

        # Читаем список папок из переменной окружения или берём умолчание
        default_folders = "transcription_canary,transcription_gemma,transcription_whisper,transcription_parakeet"
        folders_env = os.environ.get("ASR_MODEL_FOLDERS", default_folders)
        self.transcription_folders = [f.strip() for f in folders_env.split(",") if f.strip()]

        self.logger.info(f"Папки для merging: {self.transcription_folders}")

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

        logger.info("=" * 60)
        logger.info("ИНИЦИАЛИЗАЦИЯ МОДУЛЯ ОБЪЕДИНЕНИЯ ТРАНСКРИПЦИЙ")
        logger.info(f"Лог-файл: {log_file}")
        logger.info("=" * 60)
        return logger

    def parse_filename(self, filename: str) -> Tuple[str, str, str]:
        """
        Парсит имя файла. Ожидается формат:
        <base>_объединенный.txt или <base>_merged.txt
        Возвращает (base, speaker, timestamp) или (base, None, None)
        """
        base = filename
        for suffix in ['_объединенный.txt', '_merged.txt', '.txt']:
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        # Убираем возможные постфиксы _chunk...
        base = re.sub(r'__chunk\d+$', '', base)
        return base, None, None

    def merge_transcriptions(self, folder_path: Path):
        """Собирает все .txt файлы из папки в один файл с тем же именем, но с суффиксом _merged."""
        model_name = folder_path.name
        output_dir = self.script_dir / "merged_results"
        output_dir.mkdir(exist_ok=True)

        txt_files = list(folder_path.glob("*.txt"))
        if not txt_files:
            self.logger.warning(f"Нет .txt файлов в {folder_path}")
            return

        # Группируем по базовому имени
        groups = defaultdict(list)
        for f in txt_files:
            base, _, _ = self.parse_filename(f.name)
            groups[base].append(f)

        for base, files in groups.items():
            merged_lines = []
            for filepath in sorted(files):
                with open(filepath, 'r', encoding='utf-8') as fh:
                    lines = fh.readlines()
                merged_lines.extend([l.rstrip() for l in lines if l.strip()])
            if merged_lines:
                out_path = output_dir / f"{base}_{model_name}_merged.txt"
                with open(out_path, 'w', encoding='utf-8') as fh:
                    fh.write('\n'.join(merged_lines) + '\n')
                self.logger.info(f"Создан: {out_path}")

    def process_all_folders(self):
        """Обходит все заданные папки, объединяет транскрипции."""
        self.logger.info("Проверка наличия папок транскрибации...")
        existing = []
        missing = []
        for folder_name in self.transcription_folders:
            folder_path = self.script_dir / folder_name
            if folder_path.exists() and folder_path.is_dir():
                existing.append(folder_name)
            else:
                missing.append(folder_name)

        if existing:
            self.logger.info(f"Найдены папки: {', '.join(existing)}")
        if missing:
            self.logger.warning(f"Отсутствуют: {', '.join(missing)} (пропускаем)")

        if not existing:
            self.logger.error("Нет ни одной папки транскрибации!")
            return

        for folder_name in existing:
            folder_path = self.script_dir / folder_name
            self.merge_transcriptions(folder_path)

        self.logger.info("=" * 50)
        self.logger.info(f"Обработка завершена! Обработано папок: {len(existing)}")
        self.logger.info("=" * 50)

def main():
    merger = TranscriptionMerger()
    merger.process_all_folders()

if __name__ == "__main__":
    main()