import time
from pathlib import Path
from src.processor import AudioProcessorUnified

# Инициализируем процессор (он настроит логи и пр.)
proc = AudioProcessorUnified()

# Перенаправляем папку для контекстных чанков в data/context_chunks, чтобы не зависеть от диска D:
proc.context_files_dir = Path("data/context_chunks")
proc.context_files_dir.mkdir(parents=True, exist_ok=True)

# Укажи путь к длинному WAV (можно заменить на реальный звонок, если есть)
test_file = Path("data/raw/long_silence.wav")
base_name = test_file.stem

print(f"Тестовый файл: {test_file}")
start = time.time()
result = proc.create_context_chunks(test_file, base_name)
elapsed = time.time() - start

num_chunks = result.get('chunks_created', 0)
print(f"Время создания контекстных чанков: {elapsed:.1f} сек")
print(f"Создано чанков: {num_chunks}")

# Оценка вызовов LLM и токенов (грубо)
calls_llm = num_chunks
# Средний промпт (step5 + step6) ~ 3000 токенов, ответ ~ 500 токенов
total_tokens_estimate = calls_llm * 3500
print(f"Оценочное количество вызовов LLM: {calls_llm}")
print(f"Оценочный расход токенов (вход+выход): ~{total_tokens_estimate} токенов")

# Для справки: если файл 60 минут, умножаем на 2