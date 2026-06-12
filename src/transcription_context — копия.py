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
    from config_whisper import API_CONFIG
    from riva.client.proto.riva_audio_pb2 import AudioEncoding
    from riva.client.proto.riva_asr_pb2 import RecognitionConfig, RecognizeRequest
    from riva.client.proto.riva_asr_pb2_grpc import RivaSpeechRecognitionStub
    
    # Обновляем API ключ
    API_CONFIG['api_key'] = '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'
    
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("📌 Убедитесь, что все зависимости установлены:")
    print("   pip install grpcio grpcio-tools wave")
    time.sleep(1)
    sys.exit(1)

def check_required_folders():
    required_folders = [
        'D:\\VOQO\\workers\\whisperchunk_files',
        'logs', 
        'D:\\VOQO\\workers\\transcription_context'
    ]
    
    for folder in required_folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"[WHISPER] 📁 Создана папка: {folder}")

def setup_logging():
    check_required_folders()
    
    log_dir = "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"transcription_whisper_{timestamp}.log")
    
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
    language_code = API_CONFIG.get('language_code', 'ru')
    if language_code == 'ru':
        language_code = 'ru-RU'
    
    return RecognitionConfig(
        encoding=AudioEncoding.LINEAR_PCM,
        sample_rate_hertz=16000,
        language_code=language_code,
        max_alternatives=1,
        profanity_filter=False,
        enable_word_time_offsets=True,
        enable_automatic_punctuation=True,
        verbatim_transcripts=False,
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
            
            logger.info("   ├─ Отправляю запрос в облако NVIDIA (Whisper-large-v3)...")
            start_time = time.time()
            response = stub.Recognize(request, metadata=metadata)
            elapsed_time = time.time() - start_time
            logger.info(f"   ├─ Ответ получен за {elapsed_time:.2f} секунд")
            
            return response
            
        except grpc.RpcError as e:
            if attempt == max_retries - 1:
                logger.error(f"   ❌ gRPC ошибка после {max_retries} попыток: {e.details()}")
                if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                    logger.error("   ❌ Ошибка аутентификации. Проверьте API ключ и Function ID")
                elif e.code() == grpc.StatusCode.UNAVAILABLE:
                    logger.error("   ❌ Сервис недоступен")
                elif e.code() == grpc.StatusCode.INVALID_ARGUMENT:
                    logger.error("   ❌ Неверные аргументы запроса. Проверьте конфигурацию")
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
                has_word_timestamps = hasattr(alternative, 'words') and alternative.words
                
                transcript_data = {
                    'filename': filename,
                    'result_index': i,
                    'transcript': alternative.transcript.strip(),
                    'confidence': alternative.confidence,
                    'words': [],
                    'has_word_timestamps': has_word_timestamps,
                    'audio_processed': result.audio_processed
                }
                
                if has_word_timestamps:
                    for word_info in alternative.words:
                        word_data = {
                            'word': word_info.word,
                            'start_time': word_info.start_time,
                            'end_time': word_info.end_time,
                            'confidence': getattr(word_info, 'confidence', 0.0)
                        }
                        transcript_data['words'].append(word_data)
                
                results.append(transcript_data)
    
    if results:
        if results[0]['has_word_timestamps']:
            logger.info(f"   ✅ Получено {len(results)} результатов с тайм-кодами")
            if results[0]['words']:
                logger.info(f"   ├─ Слов с тайм-кодами: {len(results[0]['words'])}")
        else:
            logger.info(f"   ✅ Получено {len(results)} результатов (тайм-коды не доступны)")
    
    return results

def save_individual_result(result, output_folder, logger):
    try:
        base_name = os.path.splitext(result['filename'])[0]
        output_file = os.path.join(output_folder, f"{base_name}.txt")
        
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
        input_folder = "D:\\VOQO\\workers\\whisperchunk_files"
        output_folder = "D:\\VOQO\\workers\\transcription_context"
        
        if not os.path.exists(input_folder):
            logger.error(f"❌ Папка '{input_folder}' не найдена")
            logger.info(f"📌 Создайте папку '{input_folder}' и поместите туда WAV файлы для обработки")
            time.sleep(1)
            return
        
        audio_files = [f for f in os.listdir(input_folder) if f.lower().endswith('.wav')]
        
        if not audio_files:
            logger.error(f"❌ В папке '{input_folder}' не найдено WAV файлов")
            logger.info("📌 Поместите WAV файлы в папку 'D:\\VOQO\\workers\\whisperchunk_files'")
            time.sleep(1)
            return
        
        logger.info("🎧 NVIDIA WHISPER-LARGE-V3 - ТРАНСКРИПЦИЯ ФАЙЛОВ")
        logger.info("=" * 50)
        logger.info(f"📁 Входная папка: {input_folder}")
        logger.info(f"📁 Выходная папка: {output_folder}")
        logger.info(f"🎵 Найдено файлов: {len(audio_files)}")
        logger.info("ℹ️  Примечание: Whisper может не возвращать тайм-коды на уровне слов")
        
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
            
            language_code = API_CONFIG.get('language_code', 'ru')
            if language_code == 'ru':
                language_code = 'ru-RU'
            
            logger.info("✅ Соединение установлено")
            logger.info(f"   ├─ Сервер: {API_CONFIG['server']}")
            logger.info(f"   ├─ Function ID: {API_CONFIG['function_id']}")
            logger.info(f"   ├─ Язык: {language_code}")
            logger.info(f"   └─ Модель: Whisper-large-v3")
            
        except Exception as e:
            logger.error(f"❌ Ошибка соединения: {str(e)}")
            time.sleep(1)
            return
        
        all_results = []
        successful_files = 0
        files_with_timestamps = 0
        
        logger.info(f"\n📤 Начинаю обработку {len(valid_files)} файлов...")
        
        for file_path in valid_files:
            filename = os.path.basename(file_path)
            
            response = transcribe_with_retry(file_path, stub, metadata, logger)
            
            if response:
                results = process_transcription_results(response, filename, logger)
                if results:
                    for result in results:
                        saved_file = save_individual_result(result, output_folder, logger)
                        if saved_file:
                            all_results.append(result)
                            successful_files += 1
                            if result['has_word_timestamps']:
                                files_with_timestamps += 1
                            
                            logger.info(f"   ✅ {filename} - успешно обработан")
                            short_text = result['transcript'][:80] + "..." if len(result['transcript']) > 80 else result['transcript']
                            logger.info(f"      Текст: {short_text}")
                            if result['has_word_timestamps'] and result['words']:
                                logger.info(f"      Слов с тайм-кодами: {len(result['words'])}")
                else:
                    logger.warning(f"   ⚠️  {filename} - нет результатов распознавания")
            else:
                logger.error(f"   ❌ {filename} - ошибка транскрипции после всех попыток")
        
        channel.close()
        
        logger.info(f"\n{'='*70}")
        logger.info("🎉 ОБРАБОТКА ЗАВЕРШЕНА")
        logger.info(f"   ├─ Файлов обработано: {successful_files}/{len(valid_files)}")
        logger.info(f"   ├─ Файлов с тайм-кодами: {files_with_timestamps}/{successful_files}")
        logger.info(f"   ├─ Результатов получено: {len(all_results)}")
        logger.info(f"   ├─ Индивидуальные файлы сохранены в: {output_folder}")
        
        if files_with_timestamps == 0 and successful_files > 0:
            logger.info("\n⚠️  ВНИМАНИЕ: Тайм-коды не получены ни для одного файла")
            logger.info("   Возможные причины:")
            logger.info("   1. Модель Whisper-large-v3 не поддерживает тайм-коды на уровне слов")
            logger.info("   2. Для получения тайм-кодов используйте модель Canary или Parakeet")
        
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
        print("📌 Проверьте настройки в config_whisper.py и наличие интернет-соединения")
        time.sleep(5)