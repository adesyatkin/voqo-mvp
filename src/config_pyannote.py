"""
config_pyannote.py - Конфигурация для pyannote.ai и загрузки файлов
"""

# ==================== PYANNOTE.AI API КОНФИГУРАЦИЯ ====================
PYANNOTE_CONFIG = {
    # Ваш API ключ pyannote.ai
    "api_key": "sk_864f052db21c425c8c9c92454147f6f7",
    
    # URL API pyannote.ai
    "api_url": "https://api.pyannote.ai/v1",
    
    # Параметры диаризации
    "diarization_params": {
        "exclusive": True,           # Эксклюзивная диаризация (без пересечений)
        "numSpeakers": 2,            # Ожидаемое количество спикеров
        "confidence": True,          # Включить confidence scores
        "turnLevelConfidence": True, # Confidence для каждого сегмента
    },
    
    # Таймауты и ограничения
    "timeout": 300,                  # 5 минут на запросы
    "polling_interval": 15,          # Проверка статуса каждые 15 секунд
    "max_polling_time": 7200,        # Максимум 2 часа ожидания
    "max_file_size_mb": 1024,        # Максимальный размер файла (1 ГБ)
}

# ==================== TELEGRAM BOT API КОНФИГУРАЦИЯ ====================
TELEGRAM_CONFIG = {
    # Токен вашего бота
    "bot_token": "8044444933:AAEYQ0BRlAvlVfPk3Yc2O0S7dWl4-p4ljgc",
    
    # Ваш chat_id (получен через скрипт)
    "chat_id": 735016143,
    
    # Настройки загрузки
    "max_file_size_mb": 2000,        # Лимит Telegram (2 ГБ)
    "auto_cleanup": True,            # Автоматически удалять файлы
    "cleanup_age_hours": 24,         # Удалять файлы старше 24 часов
    "retry_attempts": 3,             # Количество попыток при ошибке
    "retry_delay": 2,                # Задержка между попытками (сек)
}

# ==================== НАСТРОЙКИ СИСТЕМЫ ====================
SYSTEM_CONFIG = {
    # Основной загрузчик
    "upload_service": "telegram",
    
    # Резервные загрузчики
    "backup_services": [],
    
    # Логирование
    "log_level": "INFO",
    "log_to_file": True,
    "log_directory": "logs",
    
    # Обработка файлов
    "temp_directory": "temp_processing",
    "keep_temp_files": False,
    "chunk_output_dir": "chunk_files",
    
    # Уведомления
    "notify_on_error": False,
    "notify_email": None,
}

# ==================== ПРОВЕРКА КОНФИГУРАЦИИ ====================
def validate_config():
    """Проверяет корректность конфигурации"""
    errors = []
    
    # Проверка pyannote
    if not PYANNOTE_CONFIG["api_key"]:
        errors.append("Не задан API ключ pyannote.ai")
    
    # Проверка Telegram
    if not TELEGRAM_CONFIG["bot_token"]:
        errors.append("Не задан токен Telegram бота")
    
    if not TELEGRAM_CONFIG["chat_id"]:
        errors.append("Не задан chat_id Telegram")
    
    if errors:
        print("❌ Ошибки в конфигурации:")
        for error in errors:
            print(f"   • {error}")
        return False
    
    print("✅ Конфигурация проверена успешно")
    return True

# Автоматическая проверка при импорте
if __name__ != "__main__":
    validate_config()