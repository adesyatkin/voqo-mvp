# config_gemma.py
# Конфигурация для модели Google Gemma 3N (через NVIDIA API)

API_CONFIG = {
    # Ваш API-ключ NVIDIA (вставлен из конфигурации Parakeet)
    "api_key": "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",

    # Модель Gemma (полное имя на NVIDIA API)
    "model": "google/gemma-3n-e4b-it",

    # URL для вызова (NVIDIA Chat Completions)
    "url": "https://integrate.api.nvidia.com/v1/chat/completions",

    # Язык (информационно, не используется в запросе)
    "language_code": "ru"
}