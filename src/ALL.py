import os
import re
from collections import defaultdict

# Пути к папкам
BASE_DIR = r"D:\VOQO\workers"
PATHS = {
    'whisper_chunk': os.path.join(BASE_DIR, "transcription_whisper"),
    'whisper_context': os.path.join(BASE_DIR, "transcription_context"),
    'canary_chunk': os.path.join(BASE_DIR, "transcription_canary"),
    'gemma_context': os.path.join(BASE_DIR, "context_gemma"),
    'canary_context': os.path.join(BASE_DIR, "context_canary"),
}
OUTPUT_DIR = os.path.join(BASE_DIR, "prom_result")

# Регулярное выражение для извлечения интервала из конца имени (9 цифр, дефис, 9 цифр)
INTERVAL_PATTERN = re.compile(r'(\d{9}-\d{9})\.txt$')

def parse_short_filename(filename):
    """
    Разбирает имя файла короткого чанка (содержит спикера).
    Возвращает (call_name, speaker, interval) или None.
    Формат: <call_name>_спикерN_<interval>.txt
    """
    match = INTERVAL_PATTERN.search(filename)
    if not match:
        return None
    interval = match.group(1)
    prefix = filename[:match.start()].rstrip('_')
    parts = prefix.split('_', 2)  # максимум 3 части: имя, спикер, возможно что-то ещё
    if len(parts) < 2:
        return None
    call_name = parts[0]
    # Проверяем, что вторая часть начинается со "спикер"
    if not parts[1].startswith('спикер'):
        return None
    speaker = parts[1]
    return call_name, speaker, interval

def parse_long_filename(filename):
    """
    Разбирает имя файла длинного контекста (не содержит спикера).
    Возвращает (call_name, interval) или None.
    Формат: <call_name>_<что-то>_<interval>.txt
    """
    match = INTERVAL_PATTERN.search(filename)
    if not match:
        return None
    interval = match.group(1)
    prefix = filename[:match.start()].rstrip('_')
    # Имя звонка — всё до первого подчёркивания
    first_underscore = prefix.find('_')
    if first_underscore == -1:
        return None
    call_name = prefix[:first_underscore]
    return call_name, interval

def read_file_content(filepath):
    """Читает содержимое текстового файла."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        print(f"Ошибка чтения файла {filepath}: {e}")
        return ""

def interval_to_start(interval):
    """Начало интервала как целое число."""
    return int(interval.split('-')[0])

def interval_to_start_end(interval):
    """(start, end) как целые числа."""
    parts = interval.split('-')
    return int(parts[0]), int(parts[1])

def find_long_contexts(short_start, long_list):
    """
    Для short_start и списка (interval, text) возвращает тексты, где interval покрывает short_start.
    """
    result = []
    for interval, text in long_list:
        start, end = interval_to_start_end(interval)
        if start <= short_start < end:
            result.append(text)
    return result

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Статистика
    stats = defaultdict(lambda: defaultdict(int))

    # Данные: data[call_name] = {
    #   'short_whisper': {interval: (speaker, text)},
    #   'short_canary': {interval: (speaker, text)},
    #   'long_whisper': [(interval, text)],
    #   'long_gemma': [(interval, text)],
    #   'long_canary': [(interval, text)],
    # }
    data = defaultdict(lambda: {
        'short_whisper': {},
        'short_canary': {},
        'long_whisper': [],
        'long_gemma': [],
        'long_canary': [],
    })

    # Сканируем все папки
    for key, folder in PATHS.items():
        if not os.path.isdir(folder):
            print(f"Предупреждение: папка {folder} не существует.")
            continue

        print(f"Сканируем {folder}...")
        files_found = 0
        for filename in os.listdir(folder):
            if not filename.lower().endswith('.txt'):
                continue
            filepath = os.path.join(folder, filename)

            if key in ('whisper_chunk', 'canary_chunk'):
                # Короткие чанки (со спикером)
                parsed = parse_short_filename(filename)
                if not parsed:
                    continue
                call_name, speaker, interval = parsed
                content = read_file_content(filepath)

                if key == 'whisper_chunk':
                    data[call_name]['short_whisper'][interval] = (speaker, content)
                    stats[call_name]['whisper_chunk'] += 1
                else:  # canary_chunk
                    data[call_name]['short_canary'][interval] = (speaker, content)
                    stats[call_name]['canary_chunk'] += 1

            else:  # длинные контексты
                parsed = parse_long_filename(filename)
                if not parsed:
                    continue
                call_name, interval = parsed
                content = read_file_content(filepath)

                if key == 'whisper_context':
                    data[call_name]['long_whisper'].append((interval, content))
                    stats[call_name]['whisper_context'] += 1
                elif key == 'gemma_context':
                    data[call_name]['long_gemma'].append((interval, content))
                    stats[call_name]['gemma_context'] += 1
                elif key == 'canary_context':
                    data[call_name]['long_canary'].append((interval, content))
                    stats[call_name]['canary_context'] += 1

            files_found += 1

        print(f"  Найдено файлов: {files_found}")

    # Вывод статистики
    print("\nСтатистика по звонкам:")
    for call_name, st in stats.items():
        print(f"Звонок: {call_name}")
        for k, v in st.items():
            print(f"  {k}: {v}")

    # Формирование выходных файлов
    for call_name, call_data in data.items():
        intervals = set(call_data['short_whisper'].keys()) | set(call_data['short_canary'].keys())
        if not intervals:
            print(f"Для звонка {call_name} нет коротких чанков, пропускаем.")
            continue

        sorted_intervals = sorted(intervals, key=interval_to_start)

        output_lines = []
        for interval in sorted_intervals:
            short_start = interval_to_start(interval)

            whisper_chunk_data = call_data['short_whisper'].get(interval)
            canary_chunk_data = call_data['short_canary'].get(interval)

            long_whisper_texts = find_long_contexts(short_start, call_data['long_whisper'])
            long_gemma_texts = find_long_contexts(short_start, call_data['long_gemma'])
            long_canary_texts = find_long_contexts(short_start, call_data['long_canary'])

            block = [f"[{interval}]"]

            # whisper_chunk
            if whisper_chunk_data:
                speaker, text = whisper_chunk_data
                block.append(f"whisper_chunk: {speaker} - {text}")
            else:
                block.append("whisper_chunk: ")

            # canary_chunk
            if canary_chunk_data:
                speaker, text = canary_chunk_data
                block.append(f"canary_chunk: {speaker} - {text}")
            else:
                block.append("canary_chunk: ")

            # whisper_context
            if long_whisper_texts:
                for idx, text in enumerate(long_whisper_texts, start=1):
                    if len(long_whisper_texts) > 1:
                        block.append(f"whisper_context{idx}: {text}")
                    else:
                        block.append(f"whisper_context: {text}")
            else:
                block.append("whisper_context: ")

            # gemma_context
            if long_gemma_texts:
                for idx, text in enumerate(long_gemma_texts, start=1):
                    if len(long_gemma_texts) > 1:
                        block.append(f"gemma_context{idx}: {text}")
                    else:
                        block.append(f"gemma_context: {text}")
            else:
                block.append("gemma_context: ")

            # canary_context
            if long_canary_texts:
                for idx, text in enumerate(long_canary_texts, start=1):
                    if len(long_canary_texts) > 1:
                        block.append(f"canary_context{idx}: {text}")
                    else:
                        block.append(f"canary_context: {text}")
            else:
                block.append("canary_context: ")

            output_lines.extend(block)
            output_lines.append("")

        # Запись в файл
        output_filename = f"{call_name}.txt"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(output_lines))
            print(f"Создан файл: {output_path}")
        except Exception as e:
            print(f"Ошибка записи файла {output_path}: {e}")

if __name__ == "__main__":
    main()