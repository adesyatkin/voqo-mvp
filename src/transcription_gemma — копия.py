import os
import sys
import base64
import logging
import time
import requests
from datetime import datetime

# Подключаем конфигурацию Gemma
try:
    from config_gemma import API_CONFIG
except ImportError:
    print("❌ Ошибка импорта config_gemma.py")
    print("📌 Создайте файл config_gemma.py с параметрами API")
    time.sleep(1)
    sys.exit(1)

def check_required_folders():
    required_folders = [
        'D:\\VOQO\\workers\\whisperchunk_files',
        'logs',
        'D:\\VOQO\\workers\\context_gemma'
    ]
    for folder in required_folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"[CONTEXT-GEMMA] 📁 Создана папка: {folder}")

def setup_logging():
    check_required_folders()
    log_dir = "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"context_gemma_{timestamp}.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

def validate_audio_file(file_path, logger):
    """Проверяет, что файл является моно WAV 16 кГц 16 бит"""
    try:
        import wave
        with wave.open(file_path, 'rb') as audio_file:
            frames = audio_file.getnframes()
            rate = audio_file.getframerate()
            channels = audio_file.getnchannels()
            sample_width = audio_file.getsampwidth()
            duration = frames / float(rate)

            logger.info(f"🔊 Аудио файл: {os.path.basename(file_path)}")
            logger.info(f"   ├─ Длительность: {duration:.2f} секунд")
            logger.info(f"   ├─ Частота: {rate} Hz")
            logger.info(f"   ├─ Каналы: {channels} {'(MONO - OK)' if channels == 1 else '(СТЕРЕО - НЕ ПОДХОДИТ!)'}")
            logger.info(f"   ├─ Размер сэмпла: {sample_width} байт {'(16-bit - OK)' if sample_width == 2 else '(НЕ 16-bit!)'}")
            logger.info(f"   └─ Формат: WAV (LINEAR_PCM)")

            return channels == 1 and sample_width == 2
    except Exception as e:
        logger.error(f"❌ Ошибка проверки файла: {str(e)}")
        return False

def transcribe_with_retry(file_path, logger, max_retries=3):
    """Отправляет аудио в Gemma через NVIDIA API, возвращает текст или None"""
    retry_delays = [3, 10, 30]

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"   ↻ Повторная попытка {attempt}/{max_retries-1} через {retry_delays[attempt-1]} сек...")
                time.sleep(retry_delays[attempt-1])

            logger.info(f"🔄 Обработка {os.path.basename(file_path)} (попытка {attempt+1})...")

            # Кодируем аудио в base64
            with open(file_path, 'rb') as f:
                audio_base64 = base64.b64encode(f.read()).decode('utf-8')

            # Формируем запрос к NVIDIA API (Gemma) с улучшенным промптом
            payload = {
                "model": API_CONFIG['model'],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": audio_base64,
                                    "format": "wav"
                                }
                            },
                            {
                                "type": "text",
                                "text": "The audio contains Russian speech. Transcribe it accurately in Russian. Output only the transcribed text, without any additional comments or explanations."
                            }
                        ]
                    }
                ],
                "max_tokens": 512,
                "temperature": 0.2,
                "top_p": 0.7,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "stream": False
            }

            headers = {
                "Authorization": f"Bearer {API_CONFIG['api_key']}",
                "Content-Type": "application/json"
            }

            logger.info("   ├─ Отправляю запрос в облако NVIDIA (Gemma)...")
            start_time = time.time()
            response = requests.post(API_CONFIG['url'], headers=headers, json=payload, timeout=120)
            elapsed = time.time() - start_time
            logger.info(f"   ├─ Ответ получен за {elapsed:.2f} сек, статус: {response.status_code}")

            if response.status_code != 200:
                logger.error(f"   ❌ Ошибка HTTP {response.status_code}: {response.text}")
                continue

            result = response.json()
            try:
                transcript = result['choices'][0]['message']['content'].strip()
            except (KeyError, IndexError) as e:
                logger.error(f"   ❌ Не удалось извлечь текст из ответа: {e}")
                continue

            if not transcript:
                logger.warning("   ⚠️  Пустой ответ от модели")
                continue

            return transcript

        except requests.exceptions.Timeout:
            logger.error("   ❌ Таймаут запроса")
        except requests.exceptions.RequestException as e:
            logger.error(f"   ❌ Ошибка сети: {e}")
        except Exception as e:
            logger.error(f"   ❌ Неизвестная ошибка: {e}")

    return None

def save_transcript(transcript, filename, output_folder, logger):
    """Сохраняет текст транскрипции в файл"""
    try:
        base_name = os.path.splitext(filename)[0]
        output_file = os.path.join(output_folder, f"{base_name}.txt")

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(transcript)

        logger.info(f"   ✅ Результат сохранен в {os.path.basename(output_file)}")
        return output_file
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения файла: {str(e)}")
        return None

def main():
    logger = setup_logging()

    try:
        input_folder = "D:\\VOQO\\workers\\whisperchunk_files"
        output_folder = "D:\\VOQO\\workers\\context_gemma"

        if not os.path.exists(input_folder):
            logger.error(f"❌ Папка '{input_folder}' не найдена")
            logger.info("📌 Убедитесь, что папка whisperchunk_files существует")
            time.sleep(1)
            return

        audio_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.wav')]
        if not audio_files:
            logger.error(f"❌ В папке '{input_folder}' нет WAV файлов")
            time.sleep(1)
            return

        logger.info("🎧 CONTEXT GEMMA - ТРАНСКРИПЦИЯ ДЛИННЫХ ЧАНКОВ")
        logger.info("=" * 50)
        logger.info(f"📁 Входная папка: {input_folder}")
        logger.info(f"📁 Выходная папка: {output_folder}")
        logger.info(f"🎵 Найдено файлов: {len(audio_files)}")

        valid_files = []
        for audio_file in sorted(audio_files):
            file_path = os.path.join(input_folder, audio_file)
            if validate_audio_file(file_path, logger):
                valid_files.append(file_path)

        if not valid_files:
            logger.error("❌ Нет валидных файлов для обработки")
            time.sleep(1)
            return

        logger.info(f"\n📤 Начинаю обработку {len(valid_files)} файлов...")

        successful_files = 0
        for file_path in valid_files:
            filename = os.path.basename(file_path)

            transcript = transcribe_with_retry(file_path, logger)

            if transcript:
                saved = save_transcript(transcript, filename, output_folder, logger)
                if saved:
                    successful_files += 1
                    logger.info(f"   ✅ {filename} - успешно обработан")
                    short_text = transcript[:100] + "..." if len(transcript) > 100 else transcript
                    logger.info(f"      Текст: {short_text}")
            else:
                logger.error(f"   ❌ {filename} - ошибка транскрипции после всех попыток")

        logger.info(f"\n{'='*70}")
        logger.info("🎉 ОБРАБОТКА ЗАВЕРШЕНА")
        logger.info(f"   ├─ Файлов обработано: {successful_files}/{len(valid_files)}")
        logger.info(f"   ├─ Результаты сохранены в: {output_folder}")
        logger.info("\n💾 Логи сохранены в папке 'logs'")

    except Exception as e:
        logger.error(f"❌ Критическая ошибка в main: {str(e)}", exc_info=True)

    print("\n" + "="*50)
    time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        print("📌 Проверьте настройки в config_gemma.py и интернет-соединение")
        time.sleep(5)