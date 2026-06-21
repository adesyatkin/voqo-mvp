import os
import re
from collections import defaultdict

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
FOLDERS = [
    'transcription_canary',
    'transcription_whisper',
    'transcription_parakeet',
    'transcription_gemma',
    'transcription_context'
]

def merge_txt_files(folder_path, suffix='_объединенный.txt'):
    groups = defaultdict(list)
    pattern = re.compile(r'^(.*?)_спикер(\d+)_(\d{9})-(\d{9})\.txt$')
    context_pattern = re.compile(r'^(.*?)_context_(\d{9})-(\d{9})\.txt$')

    for fname in os.listdir(folder_path):
        if not fname.endswith('.txt'):
            continue
        full_path = os.path.join(folder_path, fname)
        # Пытаемся извлечь базовое имя для основных чанков
        m = pattern.match(fname)
        if m:
            base = m.group(1)
        else:
            m = context_pattern.match(fname)
            if m:
                base = m.group(1)
            else:
                # Неизвестный формат — используем имя файла без расширения
                base = os.path.splitext(fname)[0]
        groups[base].append(full_path)

    for base, files in groups.items():
        files.sort()
        merged_lines = []
        for filepath in files:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read().strip()
            if text:
                merged_lines.append(text)
        if merged_lines:
            output_name = f"{base}{suffix}"
            output_path = os.path.join(folder_path, output_name)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(merged_lines) + '\n')
            print(f"[OK] {output_name} ({len(merged_lines)} чанков)")

def main():
    for folder in FOLDERS:
        folder_path = os.path.join(BASE_DIR, folder)
        if not os.path.isdir(folder_path):
            print(f"[ПРОПУСК] папка {folder} не найдена")
            continue
        print(f"\nОбработка {folder}...")
        # Для контекста используем специальный суффикс
        if folder == 'transcription_context':
            merge_txt_files(folder_path, suffix='__whisperchank_объединенный.txt')
        else:
            merge_txt_files(folder_path)

    print("\nГотово. Теперь можно запускать merging и corrector.")

if __name__ == '__main__':
    main()