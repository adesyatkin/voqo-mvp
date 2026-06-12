"""
upload_service.py - Сервис загрузки файлов через Telegram Bot API
"""

import os
import sys
import logging
import time
import hashlib
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime

# Telegram Bot API
import requests

logger = logging.getLogger('UploadService')

class TelegramUploader:
    """Загрузчик файлов через Telegram Bot API"""
    
    def __init__(self, bot_token: str, chat_id):
        """
        Args:
            bot_token: Токен бота от @BotFather
            chat_id: ID чата (число или строка)
        """
        self.bot_token = bot_token
        self.bot_api_url = f"https://api.telegram.org/bot{bot_token}"
        self.chat_id = chat_id
        
        if not self.chat_id:
            raise ValueError("❌ chat_id обязателен для работы с Telegram API")
        
        logger.info(f"✅ TelegramUploader инициализирован")
        logger.info(f"   Chat ID: {self.chat_id} (тип: {type(self.chat_id)})")
    
    def upload_file(self, file_path: Path) -> Optional[str]:
        """Загружает файл и возвращает прямую ссылку"""
        if not file_path.exists():
            logger.error(f"❌ Файл не существует: {file_path}")
            return None
        
        file_size = file_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        
        # Проверяем размер файла
        if file_size_mb > 2000:
            logger.error(f"❌ Файл слишком большой: {file_size_mb:.2f} MB")
            return None
        
        logger.info(f"📤 Загрузка в Telegram: {file_path.name} ({file_size_mb:.2f} MB)")
        
        try:
            # Загружаем файл через Telegram API
            with open(file_path, 'rb') as f:
                files = {'document': (file_path.name, f)}
                data = {'chat_id': self.chat_id}
                
                response = requests.post(
                    f"{self.bot_api_url}/sendDocument",
                    data=data,
                    files=files,
                    timeout=300  # 5 минут
                )
            
            if response.status_code != 200:
                logger.error(f"❌ Ошибка Telegram API: {response.status_code}")
                logger.error(f"   Ответ: {response.text}")
                return None
            
            result = response.json()
            
            if not result.get('ok'):
                logger.error(f"❌ Telegram API ошибка: {result}")
                return None
            
            # Получаем file_id из ответа
            document = result['result'].get('document')
            if not document:
                logger.error("❌ Не найден документ в ответе")
                return None
            
            file_id = document['file_id']
            message_id = result['result']['message_id']
            
            logger.info(f"✅ Файл загружен в Telegram")
            logger.info(f"   Message ID: {message_id}")
            logger.info(f"   File ID: {file_id[:20]}...")
            
            # Получаем прямую ссылку на файл
            direct_link = self._get_file_direct_link(file_id)
            
            if direct_link:
                logger.info(f"🔗 Прямая ссылка получена")
                logger.info(f"   URL: {direct_link[:80]}...")
                return direct_link
            else:
                logger.error("❌ Не удалось получить прямую ссылку")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("⏱️  Таймаут при загрузке файла")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки в Telegram: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _get_file_direct_link(self, file_id: str) -> Optional[str]:
        """Получает прямую ссылку на файл"""
        try:
            response = requests.get(
                f"{self.bot_api_url}/getFile",
                params={'file_id': file_id},
                timeout=30
            )
            
            if response.status_code != 200:
                logger.error(f"❌ Ошибка getFile: {response.text}")
                return None
            
            file_info = response.json()
            
            if not file_info.get('ok'):
                logger.error(f"❌ Ошибка в ответе getFile: {file_info}")
                return None
            
            file_path = file_info['result']['file_path']
            direct_link = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
            
            return direct_link
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения прямой ссылки: {e}")
            return None

class UploadManager:
    """Менеджер загрузки файлов"""
    
    def __init__(self, telegram_token: str, telegram_chat_id):
        self.uploader = TelegramUploader(telegram_token, telegram_chat_id)
        self.upload_history = []
        
        logger.info("✅ UploadManager инициализирован")
    
    def upload_audio_file(self, file_path: Path) -> Optional[str]:
        """Загружает аудиофайл и возвращает публичный URL"""
        try:
            logger.info(f"🚀 Начало загрузки: {file_path.name}")
            
            # Загружаем файл
            url = self.uploader.upload_file(file_path)
            
            if url:
                # Сохраняем информацию о загрузке
                upload_info = {
                    'timestamp': datetime.now().isoformat(),
                    'file_path': str(file_path),
                    'file_name': file_path.name,
                    'file_size': file_path.stat().st_size,
                    'uploader': 'TelegramBotAPI',
                    'url': url,
                    'status': 'success'
                }
                self.upload_history.append(upload_info)
                
                logger.info(f"✅ Файл успешно загружен")
                return url
            else:
                logger.error(f"❌ Не удалось загрузить файл")
                return None
                
        except Exception as e:
            logger.error(f"❌ Критическая ошибка: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def get_stats(self) -> Dict:
        """Статистика загрузок"""
        total = len(self.upload_history)
        successful = len([u for u in self.upload_history if u['status'] == 'success'])
        
        return {
            'total_uploads': total,
            'successful_uploads': successful,
            'failed_uploads': total - successful
        }

# Глобальный экземпляр
upload_manager = None

def init_upload_manager(token: str, chat_id):
    """Инициализирует глобальный upload_manager"""
    global upload_manager
    upload_manager = UploadManager(token, chat_id)
    return upload_manager