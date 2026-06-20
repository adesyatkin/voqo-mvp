import os
import sys
import logging
from datetime import datetime
from pathlib import Path

# Добавляем src в путь для импорта adapters
sys.path.insert(0, str(Path(__file__).parent))

from adapters import GemmaAdapter

INPUT_FOLDER = "chunk_files"
OUTPUT_FOLDER = "transcription_gemma"

def setup_logging():
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"transcription_gemma_{timestamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

def main():
    logger = setup_logging()
    logger.info("🎧 GEMMA 3N E4B - ТРАНСКРИПЦИЯ")

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

    adapter = GemmaAdapter()
    for wav_path in sorted(wav_files):
        logger.info(f"🔄 Обработка {wav_path.name}")
        try:
            transcript = adapter.transcribe(str(wav_path))
            txt_filename = wav_path.stem + ".txt"
            txt_path = output_dir / txt_filename
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(transcript.strip())
            logger.info(f"   ✅ Сохранён: {txt_path}")
        except Exception as e:
            logger.error(f"   ❌ Ошибка: {e}")

if __name__ == "__main__":
    main()