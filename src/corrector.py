#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Полный пайплайн обработки диалогов:
- Шаг 1: Объединение ASR (canary, gemma, whisper) с контекстом (whisperchank)
- Шаг 2: Сравнение ASR-версий, замена совпадающих на финальные реплики, разбивка на чанки
- Шаг 3: Восстановление текста для несовпадающих ASR-блоков с помощью контекста
- Шаг 4: Вычисление LCS для ASR-версий, фильтрация версий
- Шаг 5: Поиск наилучшего контекстного вхождения и восстановление текста
- Шаг 6: Формирование ASR-чанков, выровненных по границам чанков шага 2
- Шаг 7: Коррекция через DeepSeek API (с ротацией ключей) и склейка в итоговый файл
- Очистка промежуточных файлов (опционально)
"""

import os
import re
import time
import glob
import json
import asyncio
import logging
from bisect import bisect_right
from typing import Optional, List, Tuple, Set, Dict, Any
from pathlib import Path

# Библиотеки для шагов 2-5
import nltk
from nltk.stem.snowball import SnowballStemmer

# Для шага 7
from openai import AsyncOpenAI

# Загрузка переменных окружения из .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# ============================================================================
# НАСТРОЙКИ (адаптированы под VOQO)
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASR_DIRS = {
    'canary': os.path.join(BASE_DIR, 'transcription_canary'),
    'gemma': os.path.join(BASE_DIR, 'transcription_gemma'),  # заменён parakeet
    'whisper': os.path.join(BASE_DIR, 'transcription_whisper')
}
CONTEXT_DIR = os.path.join(BASE_DIR, 'transcription_context')
RESULT_DIR = os.path.join(BASE_DIR, 'transcription_result')
os.makedirs(RESULT_DIR, exist_ok=True)

# Параметры чанкования для шага 2
MAX_CHARS_PER_CHUNK_STEP2 = 10000

# Параметры чанкования для шага 6 (резервный метод)
MAX_CHARS_PER_CHUNK_STEP6 = 4500

# Конфигурация API DeepSeek через .env
DEEPSEEK_API_KEYS = [k for k in os.environ.get("DEEPSEEK_API_KEYS", "").split(",") if k]
if not DEEPSEEK_API_KEYS:
    raise RuntimeError("DEEPSEEK_API_KEYS не задан в .env")
API_KEY = DEEPSEEK_API_KEYS[0]

BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = "deepseek-ai/deepseek-v4-flash"   # заменён на Flash (было v4-pro)
TIMEOUT = 60   # уменьшен с 180 до 60 секунд
MAX_RETRIES = 3
RPM_LIMIT = 60
MAX_TOKENS = 32000
CONCURRENT_LIMIT = 10

request_timestamps = asyncio.Queue()

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ============================================================================
# ИНИЦИАЛИЗАЦИЯ NLTK
# ============================================================================
try:
    stemmer = SnowballStemmer("russian")
except LookupError:
    nltk.download('punkt')
    stemmer = SnowballStemmer("russian")

# ============================================================================
# ОБЩИЕ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================
def time_str_to_ms(t: str) -> int:
    if ':' in t:
        parts = t.split(':')
        hours = int(parts[0])
        minutes = int(parts[1])
        sec_part = parts[2].split('.')
        seconds = int(sec_part[0])
        millis = int(sec_part[1]) if len(sec_part) > 1 else 0
        return (hours * 3600 + minutes * 60 + seconds) * 1000 + millis
    else:
        return int(t)

def normalize_text_simple(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()

def get_stems(text: str) -> List[str]:
    words = re.findall(r'[\w-]+', text.lower())
    return [stemmer.stem(w) for w in words]

def lcs(a: List[str], b: List[str]) -> List[str]:
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    seq = []
    i, j = m, n
    while i > 0 and j > 0:
        if a[i-1] == b[j-1]:
            seq.append(a[i-1])
            i -= 1
            j -= 1
        elif dp[i-1][j] >= dp[i][j-1]:
            i -= 1
        else:
            j -= 1
    return list(reversed(seq))

def is_subseq(short: List[str], long: List[str]) -> bool:
    it = iter(long)
    return all(item in it for item in short)

# ============================================================================
# ШАГ 1: Объединение ASR и контекста (Gemma вместо Parakeet)
# ============================================================================
def step1_parse_asr_file(filepath: str) -> Dict[str, Tuple[int, str]]:
    result = {}
    pattern = re.compile(r'^\[(.*?)\]\s*-\s*Спикер\s*(\d+)\s*-\s*(.*)$')
    if not os.path.exists(filepath):
        return {}
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
    segments = []
    pattern = re.compile(r'^\[(\d{2}\d{2}\d{2}\d{3})-(\d{2}\d{2}\d{2}\d{3})\]\s*-\s*(.*)$')
    if not os.path.exists(filepath):
        return segments
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                start_str = m.group(1)
                end_str = m.group(2)
                text = m.group(3).strip()
                def to_ms(s: str) -> int:
                    h = int(s[0:2]); m = int(s[2:4]); sec = int(s[4:6]); ms = int(s[6:9])
                    return (h * 3600 + m * 60 + sec) * 1000 + ms
                start_ms = to_ms(start_str)
                end_ms = to_ms(end_str)
                segments.append((start_ms, end_ms, start_str, end_str, text))
            else:
                print(f"Предупреждение: не удалось распарсить строку контекста: {line}")
    return segments

def step1_asr_timestamp_to_ms(ts: str) -> int:
    parts = ts.split(':')
    hours = int(parts[0])
    minutes = int(parts[1])
    sec_part = parts[2].split('.')
    seconds = int(sec_part[0])
    millis = int(sec_part[1]) if len(sec_part) > 1 else 0
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + millis

def step1_asr_interval_to_ms(interval: str) -> Tuple[int, int]:
    start_str, end_str = interval.split('-')
    return step1_asr_timestamp_to_ms(start_str), step1_asr_timestamp_to_ms(end_str)

def step1_find_context_segments(context_segments: List[Tuple[int, int, str, str, str]],
                                asr_start: int, asr_end: int) -> List[Tuple[str, str, str]]:
    overlapping = []
    for ctx_start, ctx_end, orig_start, orig_end, text in context_segments:
        if ctx_end > asr_start and ctx_start < asr_end:
            overlapping.append((orig_start, orig_end, text))
    overlapping.sort(key=lambda x: x[0])
    return overlapping

def step1_process_one_file(canary_path: str, gemma_path: str, whisper_path: str,
                           context_path: str, output_path: str):
    print(f"Шаг 1: {canary_path}, {gemma_path}, {whisper_path}, {context_path}")

    canary = step1_parse_asr_file(canary_path)
    gemma = step1_parse_asr_file(gemma_path)
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
            g = gemma.get(ts)
            w = whisper.get(ts)

            start_ms, end_ms = step1_asr_interval_to_ms(ts)
            ctx_segments = step1_find_context_segments(context, start_ms, end_ms)

            out.write(f"[{ts}]\n")
            if c:
                out.write(f"canary - Спикер {c[0]} - {c[1]}\n")
            if g:
                out.write(f"gemma - Спикер {g[0]} - {g[1]}\n")
            if w:
                out.write(f"whisper - Спикер {w[0]} - {w[1]}\n")
            for orig_start, orig_end, ctx_text in ctx_segments:
                out.write(f"[{orig_start}-{orig_end}] - {ctx_text}\n")
            out.write("\n")
    print(f"Шаг 1: результат сохранён в {output_path}")

def step1_main():
    if not os.path.isdir(ASR_DIRS['canary']):
        print("Шаг 1: папка Canary не найдена")
        return
    canary_files = os.listdir(ASR_DIRS['canary'])
    pattern = re.compile(r'^(.*)_объединенный\.txt$')
    for fname in canary_files:
        m = pattern.match(fname)
        if not m:
            continue
        base = m.group(1)
        canary_path = os.path.join(ASR_DIRS['canary'], fname)
        gemma_path = os.path.join(ASR_DIRS['gemma'], fname)
        whisper_path = os.path.join(ASR_DIRS['whisper'], fname)
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
# ШАГ 2: Сравнение ASR-версий и разбивка на чанки (Gemma вместо Parakeet)
# ============================================================================
def step2_normalize_words(text: str) -> List[str]:
    text = re.sub(r'[^\w\s-]', '', text, flags=re.UNICODE)
    words = text.split()
    return [stemmer.stem(w) for w in words]

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
    if len(asr_entries) < 2:
        return False, None, None
    stems_list = []
    speakers = set()
    for model, speaker, text in asr_entries:
        stems = step2_normalize_words(text)
        stems_list.append(stems)
        speakers.add(speaker)
    first = stems_list[0]
    if not all(s == first for s in stems_list[1:]):
        return False, None, None
    speaker = next(iter(speakers)) if speakers else None
    original_text = asr_entries[0][2]
    return True, speaker, original_text

def step2_chunk_by_max_chars(blocks: List[str], max_chars: int) -> List[str]:
    chunks = []
    current_chunk = []
    current_char_count = 0
    for block in blocks:
        block_chars = len(block)
        if block_chars > max_chars:
            sub_blocks = block.split('\n')
            temp_chunk = []
            temp_count = 0
            for sub in sub_blocks:
                sub_len = len(sub)
                if temp_count + sub_len > max_chars and temp_chunk:
                    chunks.append('\n'.join(temp_chunk))
                    temp_chunk = []
                    temp_count = 0
                temp_chunk.append(sub)
                temp_count += sub_len
            if temp_chunk:
                current_chunk.extend(temp_chunk)
                current_char_count += temp_count
            continue
        if current_char_count + block_chars > max_chars and current_chunk:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = []
            current_char_count = 0
        current_chunk.append(block)
        current_char_count += block_chars + 2
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))
    return chunks

def step2_generate_chunk_filename(original_path: str, chunk_index: int) -> str:
    basename = os.path.basename(original_path)
    if basename.endswith('_step1.txt'):
        base = basename[:-9]
    else:
        base = os.path.splitext(basename)[0]
    new_name = f"{base}_chunk{chunk_index}_step2.txt"
    return os.path.join(os.path.dirname(original_path), new_name)

def step2_process_file_and_chunk(input_path: str):
    print(f"Шаг 2: обработка {input_path}")
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks_raw = content.split('\n\n')
    new_blocks = []
    for block in blocks_raw:
        lines = block.splitlines()
        if not lines:
            continue
        timestamp, asr_entries, context_entries = step2_parse_block(lines)
        match, speaker, text = step2_are_asr_versions_matching(asr_entries)
        if match and timestamp and speaker is not None:
            new_line = f"{timestamp} - Спикер {speaker} - {text}"
            new_blocks.append(new_line)
        else:
            new_blocks.append(block)
    if not new_blocks:
        print("  Нет блоков для записи.")
        return
    chunks = step2_chunk_by_max_chars(new_blocks, MAX_CHARS_PER_CHUNK_STEP2)
    print(f"  Создано чанков: {len(chunks)}")
    for idx, chunk in enumerate(chunks, start=1):
        out_path = step2_generate_chunk_filename(input_path, idx)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(chunk)
        print(f"    Чанк {idx} сохранён: {out_path}")

def step2_main():
    for fname in os.listdir(RESULT_DIR):
        if fname.endswith('_step1.txt'):
            input_path = os.path.join(RESULT_DIR, fname)
            step2_process_file_and_chunk(input_path)

# ============================================================================
# ШАГ 3: Восстановление текста для несовпадающих ASR-блоков (Gemma вместо Parakeet)
# ============================================================================
def step3_normalize_to_stems(text: str) -> List[str]:
    return get_stems(text)

def step3_find_word_positions(text: str) -> List[Tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r'[\w-]+', text)]

def step3_find_longest_continuous_sequence(stems_list: List[str], target_stems_set: Set[str]) -> Tuple[Optional[int], Optional[int]]:
    best_start = best_end = None
    best_len = 0
    cur_start = None
    for i, stem in enumerate(stems_list):
        if stem in target_stems_set:
            if cur_start is None:
                cur_start = i
            cur_len = i - cur_start + 1
            if cur_len > best_len:
                best_len = cur_len
                best_start = cur_start
                best_end = i
        else:
            cur_start = None
    return best_start, best_end

def step3_extract_text_between(full_text: str, word_positions: List[Tuple[int, int, str]],
                               left_range: Tuple[int, int], right_range: Tuple[int, int]) -> Optional[str]:
    left_start, left_end = left_range
    right_start, right_end = right_range
    if left_end >= right_start:
        return ""
    after_left = word_positions[left_end][1]
    before_right = word_positions[right_start][0]
    return full_text[after_left:before_right].strip()

def step3_clean_punctuation(text: str) -> str:
    text = re.sub(r'[^\w\s-]', '', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def step3_extract_text_from_context_entries(entries: List[str]) -> str:
    texts = []
    for entry in entries:
        m = re.match(r'^\[\d+-\d+\]\s*-\s*(.*)$', entry)
        if m:
            texts.append(m.group(1))
        else:
            texts.append(entry)
    return ' '.join(texts)

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
    with open(filepath, 'w', encoding='utf-8') as f:
        for i, block in enumerate(blocks):
            if block['type'] == 'final':
                f.write(block['text'])
            else:
                f.write(block['timestamp'] + '\n')
                for model, speaker, text in block['asr_entries']:
                    f.write(f"{model} - Спикер {speaker} - {text}\n")
                for ctx in block['context_entries']:
                    f.write(ctx + '\n')
            if i != len(blocks)-1:
                f.write('\n\n')

def step3_process_file(input_path: str, output_path: str):
    print(f"Шаг 3: {input_path} -> {output_path}")
    blocks = step3_parse_step2_file(input_path)
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block['type'] != 'asr':
            i += 1
            continue
        asr_entries = block['asr_entries']
        if len(asr_entries) < 2:
            i += 1
            continue
        stems_per_model = [set(step3_normalize_to_stems(text)) for _, _, text in asr_entries]
        common_stems = set.intersection(*stems_per_model) if stems_per_model else set()
        if common_stems:
            i += 1
            continue
        prev_idx = i - 1
        while prev_idx >= 0 and blocks[prev_idx]['type'] != 'asr':
            prev_idx -= 1
        next_idx = i + 1
        while next_idx < len(blocks) and blocks[next_idx]['type'] != 'asr':
            next_idx += 1
        if prev_idx < 0 or next_idx >= len(blocks):
            print(f"Предупреждение: для блока {block['timestamp']} нет соседних ASR-блоков")
            i += 1
            continue
        prev_block = blocks[prev_idx]
        next_block = blocks[next_idx]
        prev_stems = set()
        for _, _, text in prev_block['asr_entries']:
            prev_stems.update(step3_normalize_to_stems(text))
        next_stems = set()
        for _, _, text in next_block['asr_entries']:
            next_stems.update(step3_normalize_to_stems(text))
        context_text = step3_extract_text_from_context_entries(block['context_entries'])
        if not context_text.strip():
            print(f"Предупреждение: для блока {block['timestamp']} нет контекста")
            i += 1
            continue
        word_positions = step3_find_word_positions(context_text)
        if not word_positions:
            i += 1
            continue
        context_stems = [stemmer.stem(w.lower()) for _, _, w in word_positions]
        left_start, left_end = step3_find_longest_continuous_sequence(context_stems, prev_stems)
        if left_start is None:
            print(f"Предупреждение: для блока {block['timestamp']} не найдена левая граница")
            i += 1
            continue
        sub_stems = context_stems[left_end+1:]
        sub_positions = word_positions[left_end+1:]
        right_start_rel, right_end_rel = step3_find_longest_continuous_sequence(sub_stems, next_stems)
        if right_start_rel is None:
            print(f"Предупреждение: для блока {block['timestamp']} не найдена правая граница")
            i += 1
            continue
        right_start = left_end + 1 + right_start_rel
        right_end = left_end + 1 + right_end_rel
        between_text = step3_extract_text_between(context_text, word_positions,
                                                  (left_start, left_end), (right_start, right_end))
        if between_text is None:
            print(f"Предупреждение: для блока {block['timestamp']} не удалось извлечь текст между границами")
            i += 1
            continue
        cleaned_text = step3_clean_punctuation(between_text)
        speaker = asr_entries[0][1] if asr_entries else 1
        final_line = f"{block['timestamp']} - Спикер {speaker} - {cleaned_text}"
        blocks[i] = {'type': 'final', 'timestamp': block['timestamp'], 'text': final_line}
        i += 1
    step3_write_file(blocks, output_path)

def step3_main():
    for fname in os.listdir(RESULT_DIR):
        if fname.endswith('_step2.txt'):
            input_path = os.path.join(RESULT_DIR, fname)
            output_fname = fname.replace('_step2.txt', '_step3.txt')
            output_path = os.path.join(RESULT_DIR, output_fname)
            step3_process_file(input_path, output_path)

# ============================================================================
# ШАГ 4: Вычисление LCS для ASR-версий и фильтрация (Gemma вместо Parakeet)
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

def step4_write_file(blocks: List[Dict[str, Any]], filepath: str):
    with open(filepath, 'w', encoding='utf-8') as f:
        for i, block in enumerate(blocks):
            if block['type'] == 'final':
                f.write(block['text'])
            else:
                f.write(block['timestamp'] + '\n')
                if 'versions' in block and block['versions']:
                    for idx, ver in enumerate(block['versions'], 1):
                        f.write(f'Версия {idx}: "{" ".join(ver)}"\n')
                else:
                    for model, speaker, text in block['asr_entries']:
                        f.write(f"{model} - Спикер {speaker} - {text}\n")
                for ctx in block['context_entries']:
                    f.write(ctx + '\n')
            if i != len(blocks)-1:
                f.write('\n\n')

def step4_process_file(input_path: str, output_path: str):
    print(f"Шаг 4: {input_path} -> {output_path}")
    blocks = step4_parse_step3_file(input_path)
    new_blocks = []
    for block in blocks:
        if block['type'] == 'final':
            new_blocks.append(block)
            continue
        asr_entries = block['asr_entries']
        if len(asr_entries) < 2:
            new_blocks.append(block)
            continue
        stems_list = [get_stems(text) for _, _, text in asr_entries]
        versions_set = set()
        num = len(stems_list)
        for i in range(num):
            for j in range(i+1, num):
                common = lcs(stems_list[i], stems_list[j])
                if common:
                    versions_set.add(tuple(common))
        if not versions_set:
            continue
        versions_list = list(versions_set)
        versions_list.sort(key=len, reverse=True)
        filtered_versions = []
        for i, v in enumerate(versions_list):
            is_sub = False
            for j, w in enumerate(versions_list):
                if i != j and len(w) > len(v) and is_subseq(v, w):
                    is_sub = True
                    break
            if not is_sub:
                filtered_versions.append(v)
        filtered_versions.sort(key=lambda x: (-len(x), x))
        block['versions'] = filtered_versions
        new_blocks.append(block)
    step4_write_file(new_blocks, output_path)

def step4_main():
    for fname in os.listdir(RESULT_DIR):
        if fname.endswith('_step3.txt'):
            input_path = os.path.join(RESULT_DIR, fname)
            output_fname = fname.replace('_step3.txt', '_step4.txt')
            output_path = os.path.join(RESULT_DIR, output_fname)
            step4_process_file(input_path, output_path)

# ============================================================================
# ШАГ 5: Поиск наилучшего контекстного вхождения (Gemma вместо Parakeet)
# ============================================================================
def step5_get_words_with_positions(text: str) -> List[Tuple[str, int, int]]:
    positions = []
    for m in re.finditer(r'[\w-]+', text):
        positions.append((m.group(), m.start(), m.end()))
    return positions

def step5_find_best_subsequence(haystack_stems: List[str], needle: List[str],
                                words_pos: List[Tuple[str, int, int]]) -> Optional[Tuple[int, int]]:
    n = len(needle)
    if n == 0 or not haystack_stems:
        return None
    first_stem = needle[0]
    candidates = []
    for start in range(len(haystack_stems)):
        if haystack_stems[start] != first_stem:
            continue
        idx = start
        matched = 1
        last_idx = start
        for stem in needle[1:]:
            found = False
            for j in range(idx+1, len(haystack_stems)):
                if haystack_stems[j] == stem:
                    idx = j
                    last_idx = j
                    matched += 1
                    found = True
                    break
            if not found:
                break
        if matched == n:
            candidates.append((start, last_idx))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[1]-x[0], -x[0]))
    return candidates[0]

def step5_expand_to_punctuation(full_text: str, left_char: int, right_char: int) -> str:
    punct_chars = set('.,!?:;—–-…()[]{}"\'«»')
    new_left = left_char
    i = left_char - 1
    while i >= 0:
        if full_text[i] in punct_chars:
            new_left = i
            break
        i -= 1
    else:
        new_left = 0
    new_right = right_char
    i = right_char
    while i < len(full_text):
        if full_text[i] in punct_chars:
            new_right = i + 1
            break
        i += 1
    else:
        new_right = len(full_text)
    return full_text[new_left:new_right]

def step5_extract_text_from_context_line(line: str) -> str:
    m = re.match(r'^\[\d+-\d+\]\s*-\s*(.*)$', line)
    return m.group(1) if m else line

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
            blocks.append({
                'type': 'final',
                'timestamp': first.split(' - ')[0],
                'text': first
            })
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
    with open(filepath, 'w', encoding='utf-8') as f:
        for block in blocks:
            if block['type'] == 'final':
                f.write(block['text'] + '\n')
            else:
                f.write(block['timestamp'] + '\n')
                for ctx in block['context_entries']:
                    f.write(ctx + '\n')

def step5_process_file(step4_path: str, step3_dict: Dict[str, Any], output_path: str):
    print(f"Шаг 5: {step4_path} -> {output_path}")
    blocks = step5_parse_step4_file(step4_path)
    for idx, block in enumerate(blocks):
        if block['type'] != 'asr':
            continue
        if not block['versions']:
            continue
        timestamp = block['timestamp']
        asr_entries = step3_dict.get(timestamp, [])
        context_entries = block['context_entries']
        versions = block['versions']
        success = False
        for ver in versions:
            best_match = None
            for ctx_line in context_entries:
                ctx_text = step5_extract_text_from_context_line(ctx_line)
                if not ctx_text.strip():
                    continue
                words_pos = step5_get_words_with_positions(ctx_text)
                if not words_pos:
                    continue
                ctx_stems = [stemmer.stem(w.lower()) for w, _, _ in words_pos]
                match = step5_find_best_subsequence(ctx_stems, ver, words_pos)
                if match:
                    start_idx, end_idx = match
                    length = end_idx - start_idx
                    if best_match is None or length < best_match[3]:
                        best_match = (ctx_text, words_pos, (start_idx, end_idx), length)
                    elif length == best_match[3] and start_idx > best_match[2][0]:
                        best_match = (ctx_text, words_pos, (start_idx, end_idx), length)
            if best_match:
                ctx_text, words_pos, (start_idx, end_idx), _ = best_match
                first_word_start = words_pos[start_idx][1]
                last_word_end = words_pos[end_idx][2]
                expanded = step5_expand_to_punctuation(ctx_text, first_word_start, last_word_end)
                speaker = asr_entries[0][1] if asr_entries else 1
                final_line = f"{timestamp} - Спикер {speaker} - {expanded}"
                blocks[idx] = {'type': 'final', 'timestamp': timestamp, 'text': final_line}
                success = True
                break
        if not success:
            for ver in versions:
                for model, speaker, text in asr_entries:
                    model_stems = get_stems(text)
                    model_words = re.findall(r'[\w-]+', text)
                    model_words_pos = [(w, 0, 0) for w in model_words]
                    match = step5_find_best_subsequence(model_stems, ver, model_words_pos)
                    if match:
                        start_idx, end_idx = match
                        recovered_words = model_words[start_idx:end_idx+1]
                        recovered_text = ' '.join(recovered_words)
                        final_line = f"{timestamp} - Спикер {speaker} - {recovered_text}"
                        blocks[idx] = {'type': 'final', 'timestamp': timestamp, 'text': final_line}
                        success = True
                        break
                if success:
                    break
    step5_write_file(blocks, output_path)

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
# ШАГ 6: Формирование ASR-чанков (Gemma вместо Parakeet)
# ============================================================================
def step6_split_into_blocks(content: str) -> List[str]:
    blocks = []
    current_block = []
    for line in content.splitlines():
        if line.strip() == '':
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
        else:
            current_block.append(line)
    if current_block:
        blocks.append('\n'.join(current_block))
    return blocks

def step6_extract_first_timecode_from_block(block: str) -> Optional[str]:
    lines = block.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith('['):
            end_bracket = line.find(']')
            if end_bracket > 0:
                return line[1:end_bracket]
    return None

def step6_build_chunk_map(base_name: str, step2_dir: str) -> Tuple[Optional[List[str]], Optional[List[int]]]:
    pattern = os.path.join(step2_dir, f"{base_name}__chunk*_step2.txt")
    chunk_files = glob.glob(pattern)
    if not chunk_files:
        return None, None
    def chunk_number(fname):
        m = re.search(r'__chunk(\d+)_step2\.txt', fname)
        return int(m.group(1)) if m else 0
    chunk_files.sort(key=chunk_number)
    all_timecodes = []
    chunk_indices = []
    for idx, cf in enumerate(chunk_files, start=1):
        with open(cf, 'r', encoding='utf-8') as f:
            content = f.read()
        blocks = step6_split_into_blocks(content)
        for block in blocks:
            tc = step6_extract_first_timecode_from_block(block)
            if tc:
                all_timecodes.append(tc)
                chunk_indices.append(idx)
    return all_timecodes, chunk_indices

def step6_find_chunk_for_timestamp(ts: str, all_timecodes: List[str], chunk_indices: List[int]) -> int:
    if not all_timecodes:
        return 1
    ts_start = ts.split('-')[0]
    ts_ms = time_str_to_ms(ts_start)
    list_ms = [time_str_to_ms(tc.split('-')[0]) for tc in all_timecodes]
    pos = bisect_right(list_ms, ts_ms)
    if pos == 0:
        return 1
    elif pos == len(list_ms):
        return chunk_indices[-1]
    else:
        return chunk_indices[pos - 1]

def step6_chunk_by_max_chars(blocks: List[str], max_chars: int) -> List[str]:
    chunks = []
    current_chunk = []
    current_char_count = 0
    for block in blocks:
        block_chars = len(block)
        if current_char_count + block_chars > max_chars and current_chunk:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = []
            current_char_count = 0
        current_chunk.append(block)
        current_char_count += block_chars + 2
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))
    return chunks

def step6_generate_chunk_filename(base_name: str, chunk_index: int) -> str:
    return os.path.join(RESULT_DIR, f"{base_name}__chunk{chunk_index}_step6.txt")

def step6_parse_asr_file(filepath: str) -> Dict[str, Tuple[int, str]]:
    result = {}
    pattern = re.compile(r'^\[(.*?)\]\s*-\s*Спикер\s*(\d+)\s*-\s*(.*)$')
    if not os.path.exists(filepath):
        return {}
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
    parts = ts.split(':')
    hours = int(parts[0])
    minutes = int(parts[1])
    sec_part = parts[2].split('.')
    seconds = int(sec_part[0])
    millis = int(sec_part[1]) if len(sec_part) > 1 else 0
    return (hours * 3600 + minutes * 60 + seconds) * 1000 + millis

def step6_process_one_file(canary_path: str, gemma_path: str, whisper_path: str, base_name: str):
    print(f"Шаг 6: {canary_path}, {gemma_path}, {whisper_path}")
    canary = step6_parse_asr_file(canary_path)
    gemma = step6_parse_asr_file(gemma_path)
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
    if not os.path.isdir(ASR_DIRS['canary']):
        print("Шаг 6: папка Canary не найдена")
        return
    canary_files = os.listdir(ASR_DIRS['canary'])
    pattern = re.compile(r'^(.*)_объединенный\.txt$')
    for fname in canary_files:
        m = pattern.match(fname)
        if not m:
            continue
        base = m.group(1)
        canary_path = os.path.join(ASR_DIRS['canary'], fname)
        gemma_path = os.path.join(ASR_DIRS['gemma'], fname)
        whisper_path = os.path.join(ASR_DIRS['whisper'], fname)
        if not all(os.path.exists(p) for p in [canary_path, gemma_path, whisper_path]):
            print(f"Шаг 6: пропуск {base} – не все ASR-файлы найдены")
            continue
        step6_process_one_file(canary_path, gemma_path, whisper_path, base)

# ============================================================================
# ШАГ 7: Коррекция через DeepSeek API (с ротацией ключей)
# ============================================================================
async def step7_rate_limiter():
    now = time.time()
    while not request_timestamps.empty():
        ts = request_timestamps.get_nowait()
        if now - ts < 60:
            await request_timestamps.put(ts)
            break
    count = request_timestamps.qsize()
    if count >= RPM_LIMIT:
        oldest = await request_timestamps.get()
        wait_time = 60 - (now - oldest)
        if wait_time > 0:
            logging.info(f"Rate limit: ожидание {wait_time:.1f} сек")
            await asyncio.sleep(wait_time)
    await request_timestamps.put(time.time())

def step7_read_file_content(filepath: str) -> str:
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

def step7_build_prompt(step5_content: str, step6_content: str) -> str:
    return f"""ТВОЯ РОЛЬ: ты профессиональный корректор текстов диалогов. Твоя задача — минимальная техническая правка, а не литературное редактирование.

### ГЛАВНОЕ ПРАВИЛО ###
«Результат основного текста диалога» — это АБСОЛЮТНАЯ ИСТИНА. Твоя задача — сделать его читаемым, не меняя ни одного слова, если на то нет прямого разрешения из списка «ЧТО МОЖНО».

### ЧТО МОЖНО (Только это, ничего больше) ###
1.  **Восстановить обрывки слов.** Если в тексте явный обрыв (напр., «есте...», «потому что я...»), можно дописать слово до конца, ТОЛЬКО если это однозначно подтверждается контекстом из ASR-моделей. Нельзя дописывать целые фразы.
2.  **Удалить технические дубли.** Если в соседних тайм-кодах ОДНОГО спикера слово в слово повторяется одна и та же фраза (ошибка склейки ASR), нужно оставить эту фразу один раз в первом тайм-коде, а остальные дубли удалить. При этом нельзя добавлять союзы («и», «а») или менять порядок слов.
3.  **Исправить явные опечатки и пунктуацию.** Расставить точки, запятые, вопросительные знаки, исправить очевидные ошибки вроде «зделал» -> «сделал». Заглавные буквы в начале реплик обязательны.
4.  **Заполнить пустые реплики.** Если в строке после тайм-кода и спикера нет текста (т.е. реплика пустая), можно вставить текст из соответствующей ASR-модели (из раздела «Исходные версии ASR-моделей») для этого же тайм-кода. Если доступно несколько версий, используй текст из модели whisper (как наиболее точной). Не пытайся угадать текст самостоятельно, если ASR-версии отсутствуют.

### ЧТО НЕЛЬЗЯ (Категорически) ###
1.  **НЕЛЬЗЯ** заменять слова на синонимы или более «красивые» варианты.
2.  **НЕЛЬЗЯ** брать целые фразы из ASR-моделей (whisper, canary, gemma) и вставлять их вместо текста из основного черновика, ЗА ИСКЛЮЧЕНИЕМ случая пустой реплики (пункт 4 в разрешённом списке). ASR нужны ТОЛЬКО для понимания обрывков слов и для заполнения пустых строк.
3.  **НЕЛЬЗЯ** добавлять новые слова, которых не было в черновике (включая «по поводу», «в общем» и т.д.).
4.  **НЕЛЬЗЯ** перестраивать предложения. Порядок слов должен остаться таким, как в черновике.

### ПРИМЕРЫ ПРАВИЛЬНОЙ И НЕПРАВИЛЬНОЙ РАБОТЫ ###
*   ❌ **НЕПРАВИЛЬНО (добавление лишнего):**
    *   Черновик: `[00:00:23.405] - Спикер 2 - Ну мы сейчас будем говорить`
    *   Твой ответ: `Ну мы сейчас будем говорить по поводу этой ситуации.` (слов «по поводу» в черновике не было)
*   ✅ **ПРАВИЛЬНО (только знаки и заглавная):**
    *   Черновик: `[00:00:23.405] - Спикер 2 - Ну мы сейчас будем говорить`
    *   Твой ответ: `Ну мы сейчас будем говорить.`

*   ❌ **НЕПРАВИЛЬНО (замена на текст из Whisper):**
    *   Черновик: `[00:00:27.704] - Спикер 1 - она купила быстро низко`
    *   Whisper: `она купила быстро на низкую`
    *   Твой ответ: `Она купила быстро на низкую.` (ты взял фразу из ASR, а нужно сохранить лексику черновика — «низко»)
*   ✅ **ПРАВИЛЬНО (сохранение лексики + запятая):**
    *   Твой ответ: `Она купила быстро низко.`

*   ❌ **НЕПРАВИЛЬНО (творческая склейка с добавлением слов):**
    *   Черновик: есть два куска речи одного спикера с повтором.
    *   Твой ответ: `Я как бы согласился, но естественно, как...` (ты добавил союзы и многоточия)
*   ✅ **ПРАВИЛЬНО (просто убрать дубль):**
    *   Если одна фраза повторяется два раза в соседних тайм-кодах — оставить ее один раз и удалить тайм-код с дублем. Без изменений текста.

*   ✅ **ПРАВИЛЬНО (заполнение пустой реплики):**
    *   Черновик: `[00:00:45.804-00:00:46.435] - Спикер 2 -`
    *   ASR (whisper) для этого тайм-кода: `whisper - Спикер 2 - Угу.`
    *   Твой ответ: `[00:00:45.804-00:00:46.435] - Спикер 2 - Угу.`

### ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ ###
*   Результат выдай строго в том же формате: `[тайм-код] - Спикер № - реплика`
*   В начале ответа поставь `<result>`, в конце `</result>`.
*   Не добавляй никаких комментариев, пояснений или лишних символов, кроме исправленного текста диалога.

Исходные версии ASR-моделей, чтобы понимать где были баги. Вот они:
{step6_content}

Вот результат основного текста диалога для доработки:
{step5_content}
"""

def step7_extract_result(full_response: str) -> Optional[str]:
    pattern = r'<result>(.*?)</result>'
    match = re.search(pattern, full_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    lines = full_response.strip().splitlines()
    if lines and lines[0].startswith('[') and ' - Спикер ' in lines[0]:
        logging.warning("Теги <result> не найдены, но ответ похож на корректный текст. Используем его.")
        return full_response.strip()
    return None

async def step7_call_deepseek(prompt: str) -> Optional[str]:
    client = AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY)
    try:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            top_p=0.5,
            max_tokens=MAX_TOKENS,
            stream=True,
            timeout=TIMEOUT
        )
        full_response = []
        async for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            if delta.content is not None:
                full_response.append(delta.content)
        response_text = ''.join(full_response)
        logging.info(f"Длина ответа: {len(response_text)} символов")
        return response_text
    except Exception as e:
        logging.error(f"Ошибка API: {e}")
        return None
    finally:
        await client.close()

def step7_find_step6_file(base_dir: str, base_name: str) -> Optional[str]:
    step6_path = os.path.join(base_dir, base_name + '_step6.txt')
    if os.path.exists(step6_path):
        return step6_path
    base_without_chunk = re.sub(r'__chunk\d+$', '', base_name)
    if base_without_chunk != base_name:
        step6_path2 = os.path.join(base_dir, base_without_chunk + '_step6.txt')
        if os.path.exists(step6_path2):
            return step6_path2
    return None

def step7_get_expected_chunks(base: str) -> int:
    pattern = os.path.join(RESULT_DIR, f"{base}__chunk*_step2.txt")
    files = glob.glob(pattern)
    max_num = 0
    for f in files:
        m = re.search(r'__chunk(\d+)_step2\.txt', f)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return max_num

def step7_merge_single_file(base: str, chunks_dict: Dict[str, List[str]]):
    chunk_files = chunks_dict.get(base, [])
    if not chunk_files:
        return
    def chunk_number(fname):
        m = re.search(r'__chunk(\d+)_step7\.txt', fname)
        return int(m.group(1)) if m else 0
    chunk_files.sort(key=chunk_number)

    merged_lines = []
    for cf in chunk_files:
        with open(cf, 'r', encoding='utf-8') as f:
            content = f.read().rstrip()
        merged_lines.append(content)
    merged_text = '\n'.join(merged_lines)

    output_path = os.path.join(RESULT_DIR, f"{base}_final.txt")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(merged_text + '\n')
    logging.info(f"Создан итоговый файл: {output_path}")

async def step7_process_one_file(step5_path: str, step6_path: str, base: str, chunk_num: int,
                                  semaphore: asyncio.Semaphore,
                                  expected_chunks: Dict[str, int],
                                  processed_chunks: Dict[str, List[str]]):
    async with semaphore:
        await step7_rate_limiter()
        logging.info(f"Шаг 7: начало обработки {step5_path}")

        step5_content = step7_read_file_content(step5_path)
        step6_content = step7_read_file_content(step6_path)
        prompt = step7_build_prompt(step5_content, step6_content)
        logging.info(f"Длина промта: {len(prompt)} символов")

        output_path = os.path.join(RESULT_DIR, f"{base}__chunk{chunk_num}_step7.txt")
        success = False

        # Пробуем несколько раз с одним ключом
        for attempt in range(1, MAX_RETRIES + 1):
            logging.info(f"Попытка {attempt}/{MAX_RETRIES}")
            response = await step7_call_deepseek(prompt)
            if response is None:
                continue
            result = step7_extract_result(response)
            if result:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(result + '\n')
                logging.info(f"Успешно сохранено: {output_path}")
                success = True
                break
            else:
                logging.warning(f"Не удалось извлечь результат из ответа (попытка {attempt})")

        if not success:
            logging.error(f"Не удалось обработать {step5_path} после всех попыток")
        else:
            if base not in processed_chunks:
                processed_chunks[base] = []
            processed_chunks[base].append(output_path)

async def step7_main_async():
    expected_chunks = {}
    step2_files = glob.glob(os.path.join(RESULT_DIR, "*__chunk*_step2.txt"))
    for f in step2_files:
        m = re.search(r'^(.*?)__chunk(\d+)_step2\.txt', os.path.basename(f))
        if m:
            base = m.group(1)
            num = int(m.group(2))
            if base not in expected_chunks or expected_chunks[base] < num:
                expected_chunks[base] = num

    processed_chunks = {}
    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
    tasks = []

    for fname in os.listdir(RESULT_DIR):
        if not fname.endswith('_step5.txt'):
            continue
        base_with_chunk = fname.replace('_step5.txt', '')
        m = re.match(r'^(.*?)__chunk(\d+)$', base_with_chunk)
        if not m:
            logging.warning(f"Шаг 7: не удалось распарсить имя файла {fname}")
            continue
        base = m.group(1)
        chunk_num = int(m.group(2))
        step5_path = os.path.join(RESULT_DIR, fname)
        step6_path = step7_find_step6_file(RESULT_DIR, base_with_chunk)
        if step6_path is None:
            logging.warning(f"Шаг 7: не найден step6 для {base_with_chunk}, пропускаем")
            continue

        tasks.append(asyncio.create_task(
            step7_process_one_file(step5_path, step6_path, base, chunk_num,
                                   semaphore, expected_chunks, processed_chunks)
        ))

    if tasks:
        await asyncio.gather(*tasks)
        for base, files in processed_chunks.items():
            if files:
                step7_merge_single_file(base, processed_chunks)
        logging.info("Шаг 7: все задачи завершены, склейка выполнена.")
    else:
        logging.info("Шаг 7: нет файлов для обработки.")

def step7_main():
    asyncio.run(step7_main_async())

# ============================================================================
# ГЕНЕРАЦИЯ ФИНАЛЬНОГО JSON
# ============================================================================
def generate_final_json(base_name: str, final_txt_path: str):
    """Создаёт JSON-файл из итогового текстового файла."""
    pattern = re.compile(r'^\[(.*?)\]\s*-\s*Спикер\s*(\d+)\s*-\s*(.*)$')
    entries = []
    with open(final_txt_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = pattern.match(line)
            if m:
                time_str = m.group(1)
                speaker = int(m.group(2))
                text = m.group(3).strip()
                if '-' in time_str:
                    start_str, end_str = time_str.split('-', 1)
                else:
                    # Если нет дефиса, считаем, что это одна временная метка (начало=конец)
                    start_str = time_str
                    end_str = time_str
                start_ms = time_str_to_ms(start_str)
                end_ms = time_str_to_ms(end_str)
                entries.append({
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "speaker": speaker,
                    "text": text,
                    "source": "llm_corrected"
                })
    if entries:
        json_path = os.path.join(RESULT_DIR, f"{base_name}_final.json")
        with open(json_path, 'w', encoding='utf-8') as jf:
            json.dump(entries, jf, ensure_ascii=False, indent=2)
        logging.info(f"Создан JSON: {json_path}")

# ============================================================================
# ОЧИСТКА ПРОМЕЖУТОЧНЫХ ФАЙЛОВ
# ============================================================================
def cleanup_intermediate_files():
    patterns = [
        "*_step1.txt",
        "*_step2.txt",
        "*_step3.txt",
        "*_step4.txt",
        "*_step5.txt",
        "*_step6.txt",
        "*_step7.txt",
        "*_chunk*_step2.txt",
        "*__chunk*_step6.txt",
        "*__chunk*_step7.txt"
    ]
    removed_count = 0
    for pattern in patterns:
        for filepath in glob.glob(os.path.join(RESULT_DIR, pattern)):
            try:
                os.remove(filepath)
                logging.info(f"Удалён промежуточный файл: {os.path.basename(filepath)}")
                removed_count += 1
            except Exception as e:
                logging.error(f"Ошибка удаления {filepath}: {e}")
    logging.info(f"Очистка завершена. Удалено файлов: {removed_count}")

# ============================================================================
# ОСНОВНОЙ ПАЙПЛАЙН
# ============================================================================
def run_pipeline():
    print("=" * 60)
    print("ЗАПУСК ПАЙПЛАЙНА КОРРЕКТОРА")
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

    print("\n--- Шаг 7: коррекция через DeepSeek и склейка ---")
    step7_main()

    print("\n--- Очистка промежуточных файлов ---")
    cleanup_intermediate_files()

    # Генерация JSON из итоговых файлов
    final_files = glob.glob(os.path.join(RESULT_DIR, "*_final.txt"))
    if final_files:
        print("\n--- Генерация JSON ---")
        for final_path in final_files:
            base_name = os.path.splitext(os.path.basename(final_path))[0].replace("_final", "")
            generate_final_json(base_name, final_path)

    print("\n" + "=" * 60)
    print("ПАЙПЛАЙН КОРРЕКТОРА ЗАВЕРШЁН")
    print("=" * 60)

if __name__ == "__main__":
    run_pipeline()