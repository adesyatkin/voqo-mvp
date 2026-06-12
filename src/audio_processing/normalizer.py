import numpy as np
import soundfile as sf

class RMSNormalizer:
    def __init__(self, target_rms=0.1):
        self.target_rms = target_rms

    def normalize(self, audio, sample_rate=16000):
        # audio может быть одномерным (моно) или двумерным (стерео)
        if audio.ndim == 1:
            current_rms = np.sqrt(np.mean(audio ** 2))
            if current_rms < 1e-8:
                return audio
            gain = self.target_rms / current_rms
            return audio * gain
        else:
            # Обрабатываем каждый канал отдельно
            normalized_channels = []
            for channel in audio.T:
                current_rms = np.sqrt(np.mean(channel ** 2))
                if current_rms < 1e-8:
                    normalized_channels.append(channel)
                else:
                    gain = self.target_rms / current_rms
                    normalized_channels.append(channel * gain)
            return np.array(normalized_channels).T

def load_and_normalize(input_path, output_path, target_rms=0.1):
    """Загружает аудио (сохраняя каналы), нормализует и сохраняет."""
    audio, sr = sf.read(input_path)  # soundfile сохраняет форму (N,) или (N, channels)
    normalizer = RMSNormalizer(target_rms)
    normalized = normalizer.normalize(audio, sr)
    sf.write(output_path, normalized, sr)
    print(f"Нормализован: {input_path} -> {output_path} (каналов: {1 if normalized.ndim==1 else normalized.shape[1]})")