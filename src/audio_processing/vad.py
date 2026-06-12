import torch
import numpy as np
from silero_vad import load_silero_vad, get_speech_timestamps

class SileroVAD:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.model = load_silero_vad()

    def get_speech_segments(self, audio, pad_ms=100):
        """
        Принимает numpy-массив аудио (float32), возвращает список сегментов речи
        в формате [{'start': sec, 'end': sec}, ...].
        pad_ms – отступы в миллисекундах вокруг найденной речи, чтобы не обрезать по границе.
        """
        # Конвертируем numpy в torch тензор
        if isinstance(audio, np.ndarray):
            audio_tensor = torch.from_numpy(audio).float()
        else:
            audio_tensor = audio

        # Получаем метки речи (в секундах)
        speech_timestamps = get_speech_timestamps(
            audio_tensor,
            self.model,
            return_seconds=True
        )

        # Добавляем небольшие отступы и переводим миллисекунды в секунды
        pad_sec = pad_ms / 1000.0
        segments = []
        for ts in speech_timestamps:
            start = max(0.0, ts['start'] - pad_sec)
            end = ts['end'] + pad_sec
            segments.append({'start': round(start, 3), 'end': round(end, 3)})

        return segments