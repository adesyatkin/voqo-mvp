import time
import os
import librosa
from pyannote.audio import Pipeline

file = "data/raw/test_1min.wav"

print("Загрузка модели pyannote (первый раз скачает ~1.5 ГБ)...")
start_load = time.time()

# Токен берётся автоматически из переменной окружения HF_TOKEN
pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
print(f"Модель загружена за {time.time() - start_load:.1f} сек.")

print("Диаризация...")
start = time.time()
diarization = pipeline(file)
elapsed = time.time() - start

# Вывод результатов
for turn, _, speaker in diarization.itertracks(yield_label=True):
    print(f"[{turn.start:.1f}-{turn.end:.1f}] {speaker}")

# Метрики
duration = librosa.get_duration(path=file)
rtf = elapsed / duration
print(f"\nДлительность аудио: {duration:.1f} сек.")
print(f"Время обработки: {elapsed:.1f} сек.")
print(f"RTF (Real Time Factor): {rtf:.2f}x")
print(f"Если RTF < 3x — можно работать локально, иначе нужен платный pyannote API.")