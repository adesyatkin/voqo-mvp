#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import glob
import asyncio
import logging
from bisect import bisect_right
from typing import Optional, List, Tuple, Set, Dict, Any
import random
import nltk
from nltk.stem.snowball import SnowballStemmer
from openai import AsyncOpenAI

# ============================================================================
# НАСТРОЙКИ
# ============================================================================
BASE_DIR = r"D:\VOQO\workers"
ASR_DIRS = {
    'canary': os.path.join(BASE_DIR, 'transcription_canary'),
    'gemma': os.path.join(BASE_DIR, 'transcription_gemma'),   # заменён parakeet
    'whisper': os.path.join(BASE_DIR, 'transcription_whisper')
}
CONTEXT_DIR = os.path.join(BASE_DIR, 'transcription_context')
RESULT_DIR = os.path.join(BASE_DIR, 'transcription_result')

# ... (остальные настройки без изменений)

# ============================================================================
# ИНИЦИАЛИЗАЦИЯ NLTK
# ============================================================================
try:
    stemmer = SnowballStemmer("russian")
except LookupError:
    nltk.download('punkt')
    stemmer = SnowballStemmer("russian")

# ... (все вспомогательные функции без изменений)

# ============================================================================
# ШАГ 1: Объединение ASR и контекста (ИЗМЕНЕНО: parakeet -> gemma)
# ============================================================================
def step1_parse_asr_file(filepath: str) -> Dict[str, Tuple[int, str]]:
    # без изменений
    result = {}
    pattern = re.compile(r'^\[(.*?)\]\s*-\s*Спикер\s*(\d+)\s*-\s*(.*)$')
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                timestamp = m.group(1)
                speaker = int(m.group(2))
                text = m.group(3).strip()
                result[timestamp] = (speaker, text)
            else:
                print(f"Предупреждение: не удалось распарсить строку ASR: {line}")
    return result

def step1_parse_context_file(filepath: str) -> List[Tuple[int, int, str, str, str]]:
    # без изменений
    pass

def step1_asr_timestamp_to_ms(ts: str) -> int:
    # без изменений
    pass

def step1_asr_interval_to_ms(interval: str) -> Tuple[int, int]:
    # без изменений
    pass

def step1_find_context_segments(context_segments: List[Tuple[int, int, str, str, str]],
                                asr_start: int, asr_end: int) -> List[Tuple[str, str, str]]:
    # без изменений
    pass

def step1_process_one_file(canary_path: str, gemma_path: str, whisper_path: str,
                           context_path: str, output_path: str):
    print(f"Шаг 1: {canary_path}, {gemma_path}, {whisper_path}, {context_path}")

    canary = step1_parse_asr_file(canary_path)
    gemma = step1_parse_asr_file(gemma_path)          # заменён parakeet
    whisper = step1_parse_asr_file(whisper_path)
    context = step1_parse_context_file(context_path)

    all_timestamps = set(canary.keys()) | set(gemma.keys()) | set(whisper.keys())
    sorted_timestamps = sorted(all_timestamps, key=lambda ts: step1_asr_timestamp_to_ms(ts.split('-')[0]))

    with open(output_path, 'w', encoding='utf-8') as out:
        for ts in sorted_timestamps:
            count = sum(1 for d in [canary, gemma, whisper] if ts in d)
            if count < 2:
                continue

            c = canary.get(ts)
            g = gemma.get(ts)          # заменён p
            w = whisper.get(ts)

            start_ms, end_ms = step1_asr_interval_to_ms(ts)
            ctx_segments = step1_find_context_segments(context, start_ms, end_ms)

            out.write(f"[{ts}]\n")
            if c:
                out.write(f"canary - Спикер {c[0]} - {c[1]}\n")
            if g:
                out.write(f"gemma - Спикер {g[0]} - {g[1]}\n")      # заменён parakeet
            if w:
                out.write(f"whisper - Спикер {w[0]} - {w[1]}\n")
            for orig_start, orig_end, ctx_text in ctx_segments:
                out.write(f"[{orig_start}-{orig_end}] - {ctx_text}\n")
            out.write("\n")
    print(f"Шаг 1: результат сохранён в {output_path}")

def step1_main():
    os.makedirs(RESULT_DIR, exist_ok=True)
    canary_files = os.listdir(ASR_DIRS['canary'])
    pattern = re.compile(r'^(.*)_объединенный\.txt$')
    for fname in canary_files:
        m = pattern.match(fname)
        if not m:
            continue
        base = m.group(1)
        canary_path = os.path.join(ASR_DIRS['canary'], fname)
        gemma_path = os.path.join(ASR_DIRS['gemma'], fname)          # заменён parakeet
        whisper_path = os.path.join(ASR_DIRS['whisper'], fname)
        # Проверяем наличие всех трёх файлов (canary, gemma, whisper)
        if not all(os.path.exists(p) for p in [canary_path, gemma_path, whisper_path]):
            print(f"Шаг 1: пропуск {base} – не все ASR-файлы найдены")
            continue
        context_fname = f"{base}__whisperchank_объединенный.txt"
        context_path = os.path.join(CONTEXT_DIR, context_fname)
        if not os.path.exists(context_path):
            print(f"Шаг 1: пропуск {base} – контекстный файл не найден")
            continue
        output_path = os.path.join(RESULT_DIR, f"{base}_step1.txt")
        step1_process_one_file(canary_path, gemma_path, whisper_path, context_path, output_path)

# ============================================================================
# ШАГ 2: Сравнение ASR-версий и разбивка на чанки (добавлена модель gemma)
# ============================================================================
def step2_normalize_words(text: str) -> List[str]:
    # без изменений
    pass

def step2_parse_block(lines: List[str]) -> Tuple[Optional[str], List[Tuple[str, int, str]], List[str]]:
    timestamp = None
    asr = []
    context = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('[') and ']' in line:
            if re.match(r'^\[\d{9}-\d{9}\]', line):
                context.append(line)
            else:
                timestamp = line
        # Добавлена модель gemma в список
        elif line.startswith(('canary', 'gemma', 'whisper')):
            parts = line.split(' - ', 2)
            if len(parts) == 3:
                model = parts[0]
                speaker_part = parts[1]
                text = parts[2]
                sp_match = re.search(r'Спикер\s+(\d+)', speaker_part)
                if sp_match:
                    speaker = int(sp_match.group(1))
                    asr.append((model, speaker, text))
                else:
                    context.append(line)
            else:
                context.append(line)
        else:
            context.append(line)
    return timestamp, asr, context

def step2_are_asr_versions_matching(asr_entries: List[Tuple[str, int, str]]) -> Tuple[bool, Optional[int], Optional[str]]:
    # без изменений
    pass

def step2_chunk_by_max_chars(blocks: List[str], max_chars: int) -> List[str]:
    # без изменений
    pass

def step2_generate_chunk_filename(original_path: str, chunk_index: int) -> str:
    # без изменений
    pass

def step2_process_file_and_chunk(input_path: str):
    # без изменений (использует step2_parse_block, который уже обновлён)
    pass

def step2_main():
    for fname in os.listdir(RESULT_DIR):
        if fname.endswith('_step1.txt'):
            input_path = os.path.join(RESULT_DIR, fname)
            step2_process_file_and_chunk(input_path)

# ============================================================================
# ШАГ 3: Восстановление текста для несовпадающих ASR-блоков с помощью контекста
#        (добавлена модель gemma)
# ============================================================================
def step3_normalize_to_stems(text: str) -> List[str]:
    # без изменений
    pass

def step3_find_word_positions(text: str) -> List[Tuple[int, int, str]]:
    # без изменений
    pass

def step3_find_longest_continuous_sequence(stems_list: List[str], target_stems_set: Set[str]) -> Tuple[Optional[int], Optional[int]]:
    # без изменений
    pass

def step3_extract_text_between(full_text: str, word_positions: List[Tuple[int, int, str]],
                               left_range: Tuple[int, int], right_range: Tuple[int, int]) -> Optional[str]:
    # без изменений
    pass

def step3_clean_punctuation(text: str) -> str:
    # без изменений
    pass

def step3_extract_text_from_context_entries(entries: List[str]) -> str:
    # без изменений
    pass

def step3_parse_step2_file(filepath: str) -> List[Dict[str, Any]]:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks_raw = content.split('\n\n')
    blocks = []
    for block in blocks_raw:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith('[') and ' - Спикер ' in first:
            blocks.append({
                'type': 'final',
                'timestamp': first.split(' - ')[0],
                'text': first
            })
        else:
            timestamp = lines[0]
            asr = []
            context = []
            for line in lines[1:]:
                # Добавлена модель gemma
                if line.startswith(('canary', 'gemma', 'whisper')):
                    parts = line.split(' - ', 2)
                    if len(parts) == 3:
                        model = parts[0]
                        speaker_part = parts[1]
                        text = parts[2]
                        sp_match = re.search(r'Спикер\s+(\d+)', speaker_part)
                        if sp_match:
                            speaker = int(sp_match.group(1))
                            asr.append((model, speaker, text))
                        else:
                            context.append(line)
                    else:
                        context.append(line)
                else:
                    context.append(line)
            blocks.append({
                'type': 'asr',
                'timestamp': timestamp,
                'asr_entries': asr,
                'context_entries': context
            })
    return blocks

def step3_write_file(blocks: List[Dict[str, Any]], filepath: str):
    # без изменений
    pass

def step3_process_file(input_path: str, output_path: str):
    # без изменений (использует step3_parse_step2_file)
    pass

def step3_main():
    for fname in os.listdir(RESULT_DIR):
        if fname.endswith('_step2.txt'):
            input_path = os.path.join(RESULT_DIR, fname)
            output_fname = fname.replace('_step2.txt', '_step3.txt')
            output_path = os.path.join(RESULT_DIR, output_fname)
            step3_process_file(input_path, output_path)

# ============================================================================
# ШАГ 4: Вычисление LCS для ASR-версий и фильтрация (без изменений)
# ============================================================================
def step4_parse_step3_file(filepath: str) -> List[Dict[str, Any]]:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks_raw = content.split('\n\n')
    blocks = []
    for block in blocks_raw:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith('[') and ' - Спикер ' in first:
            blocks.append({'type': 'final', 'timestamp': first.split(' - ')[0], 'text': first})
        else:
            timestamp = lines[0]
            asr = []
            context = []
            for line in lines[1:]:
                if line.startswith(('canary', 'gemma', 'whisper')):   # добавлена gemma
                    parts = line.split(' - ', 2)
                    if len(parts) == 3:
                        model = parts[0]
                        speaker_part = parts[1]
                        text = parts[2]
                        sp_match = re.search(r'Спикер\s+(\d+)', speaker_part)
                        if sp_match:
                            speaker = int(sp_match.group(1))
                            asr.append((model, speaker, text))
                        else:
                            context.append(line)
                    else:
                        context.append(line)
                else:
                    context.append(line)
            blocks.append({
                'type': 'asr',
                'timestamp': timestamp,
                'asr_entries': asr,
                'context_entries': context
            })
    return blocks

def step4_write_file(blocks: List[Dict[str, Any]], filepath: str):
    # без изменений
    pass

def step4_process_file(input_path: str, output_path: str):
    # без изменений (использует step4_parse_step3_file)
    pass

def step4_main():
    for fname in os.listdir(RESULT_DIR):
        if fname.endswith('_step3.txt'):
            input_path = os.path.join(RESULT_DIR, fname)
            output_fname = fname.replace('_step3.txt', '_step4.txt')
            output_path = os.path.join(RESULT_DIR, output_fname)
            step4_process_file(input_path, output_path)

# ============================================================================
# ШАГ 5: Поиск наилучшего контекстного вхождения и восстановление текста
#        (добавлена модель gemma в парсинге)
# ============================================================================
def step5_get_words_with_positions(text: str) -> List[Tuple[str, int, int]]:
    # без изменений
    pass

def step5_find_best_subsequence(haystack_stems: List[str], needle: List[str],
                                words_pos: List[Tuple[str, int, int]]) -> Optional[Tuple[int, int]]:
    # без изменений
    pass

def step5_expand_to_punctuation(full_text: str, left_char: int, right_char: int) -> str:
    # без изменений
    pass

def step5_extract_text_from_context_line(line: str) -> str:
    # без изменений
    pass

def step5_parse_step3_blocks(filepath: str) -> Dict[str, List[Tuple[str, int, str]]]:
    asr_dict = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks_raw = content.split('\n\n')
    for block in blocks_raw:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith('[') and ' - Спикер ' in first:
            continue
        timestamp = lines[0]
        asr = []
        for line in lines[1:]:
            # добавлена gemma
            if line.startswith(('canary', 'gemma', 'whisper')):
                parts = line.split(' - ', 2)
                if len(parts) == 3:
                    model = parts[0]
                    speaker_part = parts[1]
                    text = parts[2]
                    sp_match = re.search(r'Спикер\s+(\d+)', speaker_part)
                    if sp_match:
                        speaker = int(sp_match.group(1))
                        asr.append((model, speaker, text))
        if asr:
            asr_dict[timestamp] = asr
    return asr_dict

def step5_parse_step4_file(filepath: str) -> List[Dict[str, Any]]:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks_raw = content.split('\n\n')
    blocks = []
    for block in blocks_raw:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith('[') and ' - Спикер ' in first:
            blocks.append({'type': 'final', 'timestamp': first.split(' - ')[0], 'text': first})
        else:
            timestamp = lines[0]
            context = []
            versions = []
            for line in lines[1:]:
                if line.startswith('Версия'):
                    m = re.match(r'Версия \d+:\s*"(.*)"', line)
                    if m:
                        stem_str = m.group(1)
                        stem_list = stem_str.split()
                        versions.append(stem_list)
                else:
                    context.append(line)
            blocks.append({
                'type': 'asr',
                'timestamp': timestamp,
                'context_entries': context,
                'versions': versions
            })
    return blocks

def step5_write_file(blocks: List[Dict[str, Any]], filepath: str):
    # без изменений
    pass

def step5_process_file(step4_path: str, step3_dict: Dict[str, Any], output_path: str):
    # без изменений (использует step3_dict, полученный через step5_parse_step3_blocks)
    pass

def step5_main():
    for fname in os.listdir(RESULT_DIR):
        if not fname.endswith('_step4.txt'):
            continue
        base = fname.replace('_step4.txt', '')
        step4_path = os.path.join(RESULT_DIR, fname)
        step3_path = os.path.join(RESULT_DIR, base + '_step3.txt')
        if not os.path.exists(step3_path):
            print(f"Шаг 5: предупреждение – не найден файл шага 3 для {base}, пропускаем")
            continue
        step3_dict = step5_parse_step3_blocks(step3_path)
        output_fname = base + '_step5.txt'
        output_path = os.path.join(RESULT_DIR, output_fname)
        step5_process_file(step4_path, step3_dict, output_path)

# ============================================================================
# ШАГ 6: Формирование ASR-чанков (заменён parakeet на gemma)
# ============================================================================
def step6_split_into_blocks(content: str) -> List[str]:
    # без изменений
    pass

def step6_extract_first_timecode_from_block(block: str) -> Optional[str]:
    # без изменений
    pass

def step6_build_chunk_map(base_name: str, step2_dir: str) -> Tuple[Optional[List[str]], Optional[List[int]]]:
    # без изменений
    pass

def step6_find_chunk_for_timestamp(ts: str, all_timecodes: List[str], chunk_indices: List[int]) -> int:
    # без изменений
    pass

def step6_chunk_by_max_chars(blocks: List[str], max_chars: int) -> List[str]:
    # без изменений
    pass

def step6_generate_chunk_filename(base_name: str, chunk_index: int) -> str:
    return os.path.join(RESULT_DIR, f"{base_name}__chunk{chunk_index}_step6.txt")

def step6_parse_asr_file(filepath: str) -> Dict[str, Tuple[int, str]]:
    # без изменений (формат без префиксов)
    result = {}
    pattern = re.compile(r'^\[(.*?)\]\s*-\s*Спикер\s*(\d+)\s*-\s*(.*)$')
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                timestamp = m.group(1)
                speaker = int(m.group(2))
                text = m.group(3).strip()
                result[timestamp] = (speaker, text)
            else:
                print(f"Предупреждение: не удалось распарсить строку ASR: {line}")
    return result

def step6_asr_timestamp_to_ms(ts: str) -> int:
    # без изменений
    pass

def step6_process_one_file(canary_path: str, gemma_path: str, whisper_path: str, base_name: str):
    print(f"Шаг 6: {canary_path}, {gemma_path}, {whisper_path}")
    canary = step6_parse_asr_file(canary_path)
    gemma = step6_parse_asr_file(gemma_path)          # заменён parakeet
    whisper = step6_parse_asr_file(whisper_path)
    all_timestamps = set(canary.keys()) | set(gemma.keys()) | set(whisper.keys())
    sorted_timestamps = sorted(all_timestamps, key=lambda ts: step6_asr_timestamp_to_ms(ts.split('-')[0]))

    blocks = []
    for ts in sorted_timestamps:
        count = sum(1 for d in [canary, gemma, whisper] if ts in d)
        if count < 2:
            continue
        block_lines = [f"[{ts}]"]
        if ts in canary:
            c = canary[ts]
            block_lines.append(f"canary - Спикер {c[0]} - {c[1]}")
        if ts in gemma:
            g = gemma[ts]
            block_lines.append(f"gemma - Спикер {g[0]} - {g[1]}")
        if ts in whisper:
            w = whisper[ts]
            block_lines.append(f"whisper - Спикер {w[0]} - {w[1]}")
        blocks.append('\n'.join(block_lines))

    if not blocks:
        print(f"Нет блоков для {base_name}")
        return

    # ... (остальная логика с разбивкой по чанкам без изменений)
    all_timecodes, chunk_indices = step6_build_chunk_map(base_name, RESULT_DIR)
    if all_timecodes and chunk_indices:
        print(f"  Найдено {len(all_timecodes)} блоков в step2, распределённых по {max(chunk_indices)} чанкам")
        chunk_dict = {}
        for block in blocks:
            tc_match = re.match(r'^\[(.*?)\]', block)
            if not tc_match:
                chunk_dict.setdefault(1, []).append(block)
                continue
            block_tc = tc_match.group(1)
            chunk_num = step6_find_chunk_for_timestamp(block_tc, all_timecodes, chunk_indices)
            chunk_dict.setdefault(chunk_num, []).append(block)
        chunk_nums = sorted(chunk_dict.keys())
        chunks = ['\n\n'.join(chunk_dict[num]) for num in chunk_nums]
        print(f"  Создано чанков: {len(chunks)}")
    else:
        print("  Предупреждение: не найдены чанки step2, разбивка по символам")
        chunks = step6_chunk_by_max_chars(blocks, MAX_CHARS_PER_CHUNK_STEP6)

    for idx, chunk in enumerate(chunks, start=1):
        out_path = step6_generate_chunk_filename(base_name, idx)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(chunk + '\n')
        print(f"    Чанк {idx} сохранён: {out_path}")

def step6_main():
    os.makedirs(RESULT_DIR, exist_ok=True)
    canary_files = os.listdir(ASR_DIRS['canary'])
    pattern = re.compile(r'^(.*)_объединенный\.txt$')
    for fname in canary_files:
        m = pattern.match(fname)
        if not m:
            continue
        base = m.group(1)
        canary_path = os.path.join(ASR_DIRS['canary'], fname)
        gemma_path = os.path.join(ASR_DIRS['gemma'], fname)          # заменён parakeet
        whisper_path = os.path.join(ASR_DIRS['whisper'], fname)
        if not all(os.path.exists(p) for p in [canary_path, gemma_path, whisper_path]):
            print(f"Шаг 6: пропуск {base} – не все ASR-файлы найдены")
            continue
        step6_process_one_file(canary_path, gemma_path, whisper_path, base)

# ============================================================================
# ШАГ 7: Коррекция через DeepSeek API (без изменений, не зависит от модели)
# ============================================================================
# ... (код шага 7 остаётся без изменений)

# ============================================================================
# ОЧИСТКА ПРОМЕЖУТОЧНЫХ ФАЙЛОВ
# ============================================================================
def cleanup_intermediate_files():
    # без изменений
    pass

# ============================================================================
# ОСНОВНОЙ ПАЙПЛАЙН
# ============================================================================
def run_pipeline():
    print("=" * 60)
    print("ЗАПУСК ПАЙПЛАЙНА")
    print("=" * 60)

    print("\n--- Шаг 1: объединение ASR и контекста ---")
    step1_main()

    print("\n--- Шаг 2: сравнение ASR-версий и разбивка на чанки ---")
    step2_main()

    print("\n--- Шаг 3: восстановление текста из контекста ---")
    step3_main()

    print("\n--- Шаг 4: вычисление LCS и фильтрация версий ---")
    step4_main()

    print("\n--- Шаг 5: поиск наилучшего контекстного вхождения ---")
    step5_main()

    print("\n--- Шаг 6: формирование ASR-чанков ---")
    step6_main()

    print("\n--- Шаг 7: коррекция через DeepSeek (с ротацией ключей) и независимая склейка ---")
    step7_main()

    print("\n--- Очистка промежуточных файлов ---")
    cleanup_intermediate_files()

    print("\n" + "=" * 60)
    print("ПАЙПЛАЙН ЗАВЕРШЁН")
    print("=" * 60)

if __name__ == "__main__":
    run_pipeline()