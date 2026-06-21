import os
import re
from pathlib import Path

# Папки с транскрипциями (те же, что использовали ранее)
FOLDERS = [
    'src/transcription_canary',
    'src/transcription_whisper',
    'src/transcription_parakeet',
    'src/transcription_gemma'
]

def fix_file(filepath):
    # Ищем в имени файла паттерн: _спикерN_начало-конец.txt
    basename = os.path.basename(filepath)
    m = re.match(r'^(.+?)_спикер(\d+)_(\d{9})-(\d{9})\.txt$', basename)
    if not m:
        # Для контекстных файлов (если вдруг есть) пропускаем
        return
    speaker = int(m.group(2))
    start_time = m.group(3)
    end_time = m.group(4)

    # Читаем исходный текст
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read().strip()
    if not text:
        return

    # Формируем строку с тайм-кодом и спикером
    new_content = f"[{start_time}-{end_time}] - Спикер {speaker} - {text}"

    # Перезаписываем файл
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
            # Пропускаем уже объединённые файлы
            if 'объединенный' in txt_file.name:
                continue
            fix_file(str(txt_file))
        print(f"  Готово.")

if __name__ == '__main__':
    main()
    print("\nВсе файлы исправлены. Теперь можно заново запустить merge_all_chunks.py и corrector.")