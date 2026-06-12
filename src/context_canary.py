import os
import sys
import wave
import grpc
import logging
import time
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    from config_canary import API_CONFIG
    from riva.client.proto.riva_audio_pb2 import AudioEncoding
    from riva.client.proto.riva_asr_pb2 import RecognitionConfig, RecognizeRequest
    from riva.client.proto.riva_asr_pb2_grpc import RivaSpeechRecognitionStub
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("📌 Убедитесь, что все зависимости установлены и файл config_canary.py существует")
    time.sleep(1)
    sys.exit(1)

def check_required_folders():
    required_folders = [
        'D:\\VOQO\\workers\\context_files',      # ← изменено
        'logs',
        'D:\\VOQO\\workers\\context_canary'
    ]
    for folder in required_folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"[CONTEXT-CANARY] 📁 Создана папка: {folder}")

def setup_logging():
    check_required_folders()
    log_dir = "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"context_canary_{timestamp}.log")

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
    try:
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

def create_recognition_config():
    return RecognitionConfig(
        encoding=AudioEncoding.LINEAR_PCM,
        sample_rate_hertz=16000,
        language_code=API_CONFIG['language_code'],
        max_alternatives=1,
        profanity_filter=False,
        enable_word_time_offsets=False,
        enable_automatic_punctuation=False,
        verbatim_transcripts=True,
        audio_channel_count=1
    )

def transcribe_with_retry(file_path, stub, metadata, logger, max_retries=3):
    retry_delays = [3, 10, 30]

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                logger.info(f"   ↻ Повторная попытка {attempt}/{max_retries-1} через {retry_delays[attempt-1]} сек...")
                time.sleep(retry_delays[attempt-1])

            logger.info(f"🔄 Обработка {os.path.basename(file_path)} (попытка {attempt+1})...")

            with open(file_path, 'rb') as f:
                audio_content = f.read()

            request = RecognizeRequest(
                config=create_recognition_config(),
                audio=audio_content
            )

            logger.info("   ├─ Отправляю запрос в облако NVIDIA (Canary)...")
            response = stub.Recognize(request, metadata=metadata)
            logger.info("   ├─ Ответ получен")

            return response

        except grpc.RpcError as e:
            if attempt == max_retries - 1:
                logger.error(f"   ❌ gRPC ошибка после {max_retries} попыток: {e.details()}")
                if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                    logger.error("   ❌ Ошибка аутентификации. Проверьте API ключ и Function ID")
                elif e.code() == grpc.StatusCode.UNAVAILABLE:
                    logger.error("   ❌ Сервис недоступен")
                return None
            else:
                logger.warning(f"   ⚠️  gRPC ошибка: {e.details()}. Пробую снова...")
        except Exception as e:
            logger.error(f"   ❌ Ошибка обработки: {str(e)}")
            return None
    return None

def process_transcription_results(response, filename, logger):
    if not response or not response.results:
        logger.warning(f"   ⚠️  Нет результатов распознавания для {filename}")
        return None

    results = []
    for i, result in enumerate(response.results):
        if result.alternatives:
            for alternative in result.alternatives:
                transcript_data = {
                    'filename': filename,
                    'result_index': i,
                    'transcript': alternative.transcript.strip(),
                    'confidence': alternative.confidence
                }
                results.append(transcript_data)

    logger.info(f"   ✅ Получено {len(results)} результатов распознавания")
    return results

def save_individual_result(result, output_folder, logger):
    try:
        base_name = os.path.splitext(result['filename'])[0]
        # Заменяем "_context_" на "_context_canary_"
        if '_context_' in base_name:
            new_base = base_name.replace('_context_', '_context_canary_')
        else:
            new_base = base_name
        output_file = os.path.join(output_folder, f"{new_base}.txt")

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result['transcript'].strip())

        logger.info(f"   ✅ Результат сохранен в {os.path.basename(output_file)}")
        return output_file
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения файла: {str(e)}")
        return None

def main():
    logger = setup_logging()

    try:
        input_folder = "D:\\VOQO\\workers\\context_files"          # ← изменено
        output_folder = "D:\\VOQO\\workers\\context_canary"

        if not os.path.exists(input_folder):
            logger.error(f"❌ Папка '{input_folder}' не найдена")
            logger.info("📌 Убедитесь, что папка context_files существует")
            time.sleep(1)
            return

        audio_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.wav')]
        if not audio_files:
            logger.error(f"❌ В папке '{input_folder}' нет WAV файлов")
            time.sleep(1)
            return

        logger.info("🎧 CONTEXT CANARY - ТРАНСКРИПЦИЯ КОНТЕКСТНЫХ ЧАНКОВ")
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

        try:
            logger.info("\n🔗 Устанавливаю соединение с NVIDIA Cloud...")
            channel_credentials = grpc.ssl_channel_credentials()
            channel = grpc.secure_channel(API_CONFIG['server'], channel_credentials)
            stub = RivaSpeechRecognitionStub(channel)

            metadata = [
                ('authorization', f"Bearer {API_CONFIG['api_key']}"),
                ('function-id', API_CONFIG['function_id'])
            ]

            logger.info("✅ Соединение установлено")
            logger.info(f"   ├─ Сервер: {API_CONFIG['server']}")
            logger.info(f"   ├─ Function ID: {API_CONFIG['function_id']}")
            logger.info(f"   └─ Язык: {API_CONFIG['language_code']}")

        except Exception as e:
            logger.error(f"❌ Ошибка соединения: {str(e)}")
            time.sleep(1)
            return

        successful_files = 0
        logger.info(f"\n📤 Начинаю обработку {len(valid_files)} файлов...")

        for file_path in valid_files:
            filename = os.path.basename(file_path)

            response = transcribe_with_retry(file_path, stub, metadata, logger)

            if response:
                results = process_transcription_results(response, filename, logger)
                if results:
                    for result in results:
                        saved = save_individual_result(result, output_folder, logger)
                        if saved:
                            successful_files += 1
                            logger.info(f"   ✅ {filename} - успешно обработан")
                            short_text = result['transcript'][:100] + "..." if len(result['transcript']) > 100 else result['transcript']
                            logger.info(f"      Текст: {short_text}")
                else:
                    logger.warning(f"   ⚠️  {filename} - нет результатов распознавания")
            else:
                logger.error(f"   ❌ {filename} - ошибка транскрипции после всех попыток")

        channel.close()

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
        print("📌 Проверьте настройки в config_canary.py и интернет-соединение")
        time.sleep(5)