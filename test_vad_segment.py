import soundfile as sf
from src.audio_processing.vad import SileroVAD

# Загрузи нормализованный файл
audio, sr = sf.read('data/processed/Телеграм_norm.wav')

# Запусти VAD
vad = SileroVAD()
segments = vad.get_speech_segments(audio)

print(f'Всего сегментов: {len(segments)}')

# Возьми первый сегмент (0.0 - 3.8 сек) и сохрани его
start_sec = segments[0]['start']
end_sec = segments[0]['end']
start_idx = int(start_sec * sr)
end_idx = int(end_sec * sr)
sf.write('data/processed/test_segment.wav', audio[start_idx:end_idx], sr)
print(f'Сегмент {start_sec}-{end_sec} сохранён в data/processed/test_segment.wav')