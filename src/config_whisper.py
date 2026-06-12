# config.py
# Конфигурация для подключения к NVIDIA Cloud Functions

API_CONFIG = {
    "server": "grpc.nvcf.nvidia.com:443",
    "use_ssl": True,
    "function_id": "b702f636-f60c-4a3d-a6f4-f3568c13bd7d",
    "language_code": "ru",  # русский язык
    "api_key": "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"  # Ваш ключ
}

# Настройки аудио
AUDIO_CONFIG = {
    "sample_rate": 16000,  # стандартная частота дискретизации
    "channels": 1,         # моно-аудио
}