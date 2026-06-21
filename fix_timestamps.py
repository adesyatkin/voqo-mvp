import os
import re
from pathlib import Path

FOLDERS = [
    'src/transcription_canary',
    'src/transcription_whisper',
    'src/transcription_parakeet',
    'src/transcription_gemma'
]

def ms_to_hms(ms):
    """Конвертирует миллисекунды в HH:MM:SS.mmm"""
    s = ms // 1000
    m = s // 60
    h = m // 60
    return f"{h:02d}:{m%60:02d}:{s%60:02d}.{ms%1000:03d}"

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read().strip()
    if not content:
        return

    # Ищем паттерн [число-число] - Спикер N - текст
    m = re.match(r'^\[(\d{9})-(\d{9})\]\s*-\s*Спикер\s+(\d+)\s*-\s*(.*)$', content)
    if not m:
        return  # не наш формат, пропускаем

    start_ms = int(m.group(1))
    end_ms = int(m.group(2))
    speaker = m.group(3)
    text = m.group(4)

    start_hms = ms_to_hms(start_ms)
    end_hms = ms_to_hms(end_ms)

    new_content = f"[{start_hms}-{end_hms}] - Спикер {speaker} - {text}"

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content + '\n')

def main():
    for folder in FOLDERS:
        folder_path = Path(folder)
        if not folder_path.exists():
            print(f"[ПРОПУСК] {folder}")
            continue
        print(f"Обработка {folder}...")
        for txt_file in folder_path.glob("*.txt"):
            if 'объединенный' in txt_file.name:
                continue
            fix_file(str(txt_file))
        print("  Готово.")

if __name__ == '__main__':
    main()
    print("\nФормат времени исправлен. Теперь запускайте merge_all_chunks.py и corrector.")