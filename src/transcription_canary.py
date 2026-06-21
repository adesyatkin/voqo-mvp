import os
import sys
import logging
import re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from adapters import CanaryAdapter

INPUT_FOLDER = "chunk_files"
OUTPUT_FOLDER = "transcription_canary"
ADAPTER_CLASS = CanaryAdapter

def setup_logging():
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"transcription_{OUTPUT_FOLDER}_{timestamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

def ms_to_hms(ms):
    """Конвертирует миллисекунды в HH:MM:SS.mmm"""
    s = ms // 1000
    m = s // 60
    h = m // 60
    return f"{h:02d}:{m%60:02d}:{s%60:02d}.{ms%1000:03d}"

def main():
    logger = setup_logging()
    logger.info(f"🎧 {ADAPTER_CLASS.__name__} - ТРАНСКРИПЦИЯ")

    input_dir = Path(__file__).parent / INPUT_FOLDER
    output_dir = Path(__file__).parent / OUTPUT_FOLDER
    output_dir.mkdir(exist_ok=True)

    if not input_dir.exists():
        logger.error(f"❌ Папка {input_dir} не найдена")
        return

    wav_files = list(input_dir.glob("*.wav"))
    if not wav_files:
        logger.warning(f"⚠️ Нет WAV-файлов в {input_dir}")
        return

    adapter = ADAPTER_CLASS()
    pattern = re.compile(r'_спикер(\d+)_(\d{9})-(\d{9})\.wav$')

    for wav_path in sorted(wav_files):
        m = pattern.search(wav_path.name)
        if not m:
            logger.warning(f"Пропущен {wav_path.name} – не распознан формат имени")
            continue
        speaker = int(m.group(1))
        start_ms = int(m.group(2))
        end_ms = int(m.group(3))
        logger.info(f"🔄 Обработка {wav_path.name}")
        try:
            transcript = adapter.transcribe(str(wav_path)).strip()
            start_hms = ms_to_hms(start_ms)
            end_hms = ms_to_hms(end_ms)
            line = f"[{start_hms}-{end_hms}] - Спикер {speaker} - {transcript}"
            txt_filename = wav_path.stem + ".txt"
            txt_path = output_dir / txt_filename
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(line + "\n")
            logger.info(f"   ✅ Сохранён: {txt_path}")
        except Exception as e:
            logger.error(f"   ❌ Ошибка: {e}")

if __name__ == "__main__":
    main()