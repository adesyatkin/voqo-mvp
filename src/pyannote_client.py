"""
pyannote_client.py - Клиент для работы с pyannote.ai API
Исправленная версия на основе тестов
"""

import requests
import time
import json
import logging
import uuid
import re
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class PyannoteClient:
    """Клиент для работы с API pyannote.ai"""
    
    def __init__(self, api_key: str, api_url: str = "https://api.pyannote.ai/v1"):
        self.api_key = api_key
        self.api_url = api_url.rstrip('/')
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def test_connection(self) -> bool:
        """Тестирует подключение к API"""
        try:
            # Используем эндпоинт diarize для проверки
            test_data = {
                "url": "https://files.pyannote.ai/marklex1min.wav",
                "numSpeakers": 1
            }
            
            response = requests.post(
                f"{self.api_url}/diarize",
                headers=self.headers,
                json=test_data,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info("✅ Подключение к pyannote.ai установлено")
                return True
            else:
                logger.warning(f"⚠️  API недоступен: статус {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к pyannote.ai: {e}")
            return False
    
    def validate_object_key(self, object_key: str) -> bool:
        """
        Проверяет, соответствует ли object-key требованиям API
        
        Требования из документации:
        - Формат: media://object-key
        - object-key может содержать буквы, цифры, дефисы, подчеркивания
        - Должен быть уникальным в пределах команды
        """
        pattern = r'^media://[a-zA-Z0-9\-_]+$'
        if not re.match(pattern, object_key):
            logger.error(f"❌ Неверный формат object-key: {object_key}")
            logger.error("Ожидаемый формат: media://имя-файла-123")
            return False
        return True
    
    def upload_audio(self, audio_file: Path, object_key: str = None) -> Optional[str]:
        """
        Загружает аудиофайл на сервер pyannote
        
        Args:
            audio_file: Путь к аудиофайлу
            object_key: Ключ объекта (если None, будет сгенерирован)
        
        Returns:
            media:// ключ для использования в API
        """
        try:
            # Генерируем ключ объекта
            if not object_key:
                # Создаем безопасное имя файла
                safe_name = re.sub(r'[^a-zA-Z0-9\-_]', '_', audio_file.stem)
                object_key = f"media://{safe_name}_{uuid.uuid4().hex[:8]}"
            
            # Проверяем формат
            if not self.validate_object_key(object_key):
                # Пробуем исправить
                clean_key = object_key.replace('://', '://')  # Убеждаемся в правильном формате
                if not self.validate_object_key(clean_key):
                    # Используем простой вариант
                    object_key = f"media://audio_{uuid.uuid4().hex[:12]}"
            
            logger.info(f"📤 Загрузка файла {audio_file.name} как {object_key}")
            
            # 1. Получаем pre-signed URL для загрузки
            logger.debug(f"Запрос pre-signed URL для {object_key}")
            
            data = {"url": object_key}
            response = requests.post(
                f"{self.api_url}/media/input",
                headers=self.headers,
                json=data,
                timeout=30
            )
            
            logger.debug(f"Статус ответа: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ Ошибка получения pre-signed URL: {response.status_code}")
                logger.error(f"Ответ: {response.text}")
                
                # Пробуем проанализировать ошибку
                try:
                    error_data = response.json()
                    if 'errors' in error_data:
                        for error in error_data['errors']:
                            logger.error(f"  - {error.get('field')}: {error.get('message')}")
                except:
                    pass
                
                return None
            
            try:
                result = response.json()
                logger.debug(f"JSON ответ: {result}")
                
                # Получаем pre-signed URL из ответа
                presigned_url = result.get('url')
                if not presigned_url:
                    logger.error(f"❌ Не найден pre-signed URL в ответе: {result}")
                    return None
                
                logger.info(f"✅ Получен pre-signed URL")
                
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON: {e}")
                logger.error(f"Ответ: {response.text}")
                return None
            
            # 2. Загружаем файл на pre-signed URL
            file_size_mb = audio_file.stat().st_size / (1024 * 1024)
            logger.info(f"📤 Загрузка файла {audio_file.name} ({file_size_mb:.2f} МБ)...")
            
            try:
                with open(audio_file, 'rb') as f:
                    file_data = f.read()
                
                upload_response = requests.put(
                    presigned_url,
                    data=file_data,
                    headers={"Content-Type": "audio/wav"},  # Меняем на audio/wav для WAV файлов
                    timeout=300
                )
                
                logger.debug(f"Статус загрузки: {upload_response.status_code}")
                
                if upload_response.status_code not in [200, 201, 204]:
                    logger.error(f"❌ Ошибка загрузки файла: {upload_response.status_code}")
                    logger.error(f"Ответ загрузки: {upload_response.text[:200]}")
                    return None
                
                logger.info(f"✅ Файл успешно загружен: {object_key}")
                return object_key
                
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки файла: {e}")
                return None
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки аудио: {e}")
            return None
    
    def upload_audio_direct(self, audio_file: Path) -> Optional[str]:
        """
        Альтернативный метод загрузки: использование прямого URL вместо загрузки на pyannote
        Нужен публичный URL файла
        """
        try:
            # Этот метод требует, чтобы файл был доступен по публичному URL
            # Для теста используем пример из документации
            public_url = "https://files.pyannote.ai/marklex1min.wav"
            
            logger.info(f"📤 Использую публичный URL: {public_url}")
            return public_url
            
        except Exception as e:
            logger.error(f"❌ Ошибка альтернативной загрузки: {e}")
            return None
    
    def start_diarization(self, media_key: str, num_speakers: int = 2, 
                         exclusive: bool = True) -> Optional[str]:
        """
        Запускает задачу диаризации
        
        Args:
            media_key: media:// ключ загруженного файла или публичный URL
            num_speakers: Ожидаемое количество спикеров
            exclusive: Непересекающиеся сегменты
        
        Returns:
            ID задачи диаризации
        """
        try:
            # Определяем, это media:// ключ или публичный URL
            if media_key.startswith('media://'):
                url_param = media_key
            else:
                url_param = media_key
            
            data = {
                "url": url_param,
                "exclusive": exclusive,
                "numSpeakers": num_speakers,
                "confidence": True,
                "turnLevelConfidence": True
            }
            
            logger.info(f"🔧 Запуск диаризации с параметрами: {num_speakers} спикера(ов)")
            logger.debug(f"URL: {url_param}")
            
            response = requests.post(
                f"{self.api_url}/diarize",
                headers=self.headers,
                json=data,
                timeout=30
            )
            
            logger.debug(f"Статус ответа: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ Ошибка запуска диаризации: {response.status_code}")
                logger.error(f"Ответ: {response.text}")
                return None
            
            try:
                result = response.json()
                logger.debug(f"JSON ответ: {result}")
                
                job_id = result.get("jobId")
                if not job_id:
                    logger.error("❌ Не получен ID задачи")
                    return None
                
                logger.info(f"✅ Задача диаризации создана: {job_id}")
                return job_id
                
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON: {e}")
                logger.error(f"Ответ: {response.text}")
                return None
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска диаризации: {e}")
            return None
    
    def get_job_status(self, job_id: str) -> Optional[Dict]:
        """
        Получает статус задачи
        
        Args:
            job_id: ID задачи
        
        Returns:
            Словарь со статусом задачи
        """
        try:
            response = requests.get(
                f"{self.api_url}/jobs/{job_id}",
                headers=self.headers,
                timeout=30
            )
            
            logger.debug(f"Статус запроса: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ Ошибка получения статуса: {response.status_code}")
                return None
            
            try:
                result = response.json()
                return result
                
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON: {e}")
                return None
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения статуса: {e}")
            return None
    
    def wait_for_completion(self, job_id: str, timeout: int = 1200, 
                           interval: int = 10) -> Optional[Dict]:
        """
        Ожидает завершения задачи
        
        Args:
            job_id: ID задачи
            timeout: Максимальное время ожидания (секунды)
            interval: Интервал опроса (секунды)
        
        Returns:
            Результат задачи или None при ошибке/таймауте
        """
        start_time = time.time()
        
        logger.info(f"⏳ Ожидание завершения диаризации (ID: {job_id}, таймаут: {timeout} сек)...")
        
        while time.time() - start_time < timeout:
            job_status = self.get_job_status(job_id)
            
            if not job_status:
                logger.warning("⚠️  Не удалось получить статус задачи")
                time.sleep(interval)
                continue
            
            status = job_status.get("status")
            
            if status == "succeeded":
                logger.info("✅ Диаризация успешно завершена")
                output = job_status.get("output", {})
                return output
            elif status in ["failed", "canceled"]:
                logger.error(f"❌ Диаризация завершилась со статусом: {status}")
                logger.error(f"Детали: {job_status.get('error', 'Нет деталей')}")
                return None
            else:
                elapsed = time.time() - start_time
                if elapsed % 30 == 0:  # Логируем каждые 30 секунд
                    logger.info(f"⏳ Статус: {status} (ожидание: {elapsed:.0f} сек)")
                time.sleep(interval)
        
        logger.error(f"❌ Таймаут ожидания диаризации ({timeout} сек)")
        return None
    
    def get_diarization_segments(self, job_result: Dict) -> List[Dict]:
        """
        Извлекает сегменты диаризации из результата
        
        Args:
            job_result: Результат задачи диаризации
        
        Returns:
            Список сегментов диаризации
        """
        try:
            # Пытаемся получить эксклюзивную диаризацию
            segments = job_result.get("exclusiveDiarization", [])
            
            # Если нет эксклюзивной, берем обычную
            if not segments:
                segments = job_result.get("diarization", [])
            
            # Форматируем сегменты
            formatted_segments = []
            for segment in segments:
                formatted_segments.append({
                    "speaker": segment.get("speaker", "UNKNOWN"),
                    "start": segment.get("start", 0),
                    "end": segment.get("end", 0),
                    "confidence": segment.get("confidence", {}),
                    "duration": segment.get("end", 0) - segment.get("start", 0)
                })
            
            logger.info(f"📊 Извлечено {len(formatted_segments)} сегментов диаризации")
            return formatted_segments
            
        except Exception as e:
            logger.error(f"❌ Ошибка извлечения сегментов: {e}")
            return []