import os
import shutil
import subprocess
import logging
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

def setup_logging():
    """Настройка логирования с timestamp и уровнем детализации"""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)  # Создаем папку logs если не существует
    
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(log_dir / 'distributor.log', encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

def get_supported_extensions():
    """Возвращает набор поддерживаемых аудио/видео расширений"""
    return {
        # Аудио форматы
        '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', 
        '.aiff', '.ape', '.opus', '.amr', '.mp2', '.mp1', '.ac3',
        '.dts', '.pcm', '.adpcm', '.gsm', '.voc', '.au', '.snd',
        
        # Видео форматы (могут содержать аудио)
        '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm',
        '.mpeg', '.mpg', '.m4v', '.3gp', '.3g2', '.f4v', '.m2ts',
        '.mts', '.ts', '.mxf', '.rm', '.rmvb', '.asf', '.divx',
        '.vob', '.ogv', '.qt', '.yuv', '.m2v'
    }

def should_process_file(file_path):
    """Проверяет, нужно ли обрабатывать файл на основе расширения и содержимого"""
    file_ext = Path(file_path).suffix.lower()
    
    # Если расширение не в списке поддерживаемых - пропускаем
    if file_ext not in get_supported_extensions():
        logging.debug(f"Файл {Path(file_path).name}: неподдерживаемое расширение {file_ext}")
        return False
    
    # Для поддерживаемых расширений проверяем наличие аудиодорожек
    return get_audio_streams(file_path)

def get_audio_streams(file_path):
    """Проверка наличия аудиодорожек с помощью ffprobe"""
    try:
        cmd = [
            'ffprobe', 
            '-v', 'error',
            '-select_streams', 'a',
            '-show_entries', 'stream=codec_type,channels,sample_rate',
            '-of', 'json',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Парсим JSON вывод
        probe_data = json.loads(result.stdout)
        
        # Проверяем наличие аудиопотоков
        has_audio = ('streams' in probe_data and 
                    len(probe_data['streams']) > 0 and
                    any(stream.get('codec_type') == 'audio' 
                        for stream in probe_data['streams']))
        
        if has_audio:
            audio_info = []
            for stream in probe_data['streams']:
                if stream.get('codec_type') == 'audio':
                    channels = stream.get('channels', 'N/A')
                    sample_rate = stream.get('sample_rate', 'N/A')
                    audio_info.append(f"channels:{channels} sample_rate:{sample_rate}")
            
            logging.info(f"Файл {Path(file_path).name}: найдены аудиодорожки [{', '.join(audio_info)}]")
        else:
            logging.info(f"Файл {Path(file_path).name}: аудиодорожки не найдены")
        
        return has_audio
        
    except subprocess.CalledProcessError as e:
        logging.warning(f"Файл {Path(file_path).name}: ошибка ffprobe (возможно поврежден или не медиафайл)")
        return False
    except json.JSONDecodeError as e:
        logging.error(f"Файл {Path(file_path).name}: ошибка парсинга JSON вывода ffprobe")
        return False
    except Exception as e:
        logging.error(f"Ошибка проверки {file_path}: {str(e)}")
        return False

def process_file(file_path, internal_dir, garbage_dir):
    """Обработка отдельного файла"""
    try:
        filename = Path(file_path).name
        logging.info(f"Начало обработки файла: {filename}")

        if should_process_file(file_path):
            dest_path = internal_dir / filename
            shutil.copy2(file_path, dest_path)
            logging.info(f"✓ Скопирован в internal_files: {filename} (имеет аудио)")
            return True, filename
        else:
            dest_path = garbage_dir / filename
            shutil.move(file_path, dest_path)
            logging.info(f"→ Перемещен в garbage: {filename} (нет аудио/неподдерживаемый формат)")
            return False, filename

    except Exception as e:
        logging.error(f"✗ Ошибка обработки файла {file_path}: {str(e)}")
        return None, filename

def main():
    setup_logging()
    logging.info("=" * 60)
    logging.info("ЗАПУСК МОДУЛЯ DISTRIBUTOR")
    logging.info("=" * 60)
    
    # Определение путей
    base_dir = Path(__file__).parent  # Изменено: теперь берем только родительскую папку (cleaner)
    input_dir = base_dir / "input_files"  # Изменено: теперь D:\VOQO\cleaner\input_files
    internal_dir = base_dir / "internal_files"
    garbage_dir = base_dir / "garbage"  # Изменено: теперь D:\VOQO\cleaner\garbage
    
    # Создание директорий если отсутствуют
    internal_dir.mkdir(exist_ok=True)
    garbage_dir.mkdir(exist_ok=True)
    
    # Проверка существования исходной директории
    if not input_dir.exists():
        logging.error(f"✗ Исходная директория не найдена: {input_dir}")
        logging.info("Создайте папку 'input_files' в папке cleaner")
        return

    # Проверка доступности ffprobe
    try:
        subprocess.run(['ffprobe', '-version'], capture_output=True, check=True)
        logging.info("✓ FFprobe доступен")
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.error("✗ FFprobe не найден! Убедитесь, что FFmpeg установлен и добавлен в PATH")
        logging.info("Запустите setup_windows.bat для установки FFmpeg")
        return

    # Сбор файлов для обработки
    files_to_process = []
    for item in input_dir.iterdir():
        if item.is_file():
            files_to_process.append(item)
    
    if not files_to_process:
        logging.info("ℹ Нет файлов для обработки в папке input_files")
        return

    logging.info(f"📁 Найдено файлов для обработки: {len(files_to_process)}")
    
    # Параллельная обработка файлов
    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(process_file, file_path, internal_dir, garbage_dir): file_path 
            for file_path in files_to_process
        }
        
        # Сбор результатов
        processed_files = []
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                has_audio, filename = result
                if has_audio is not None:
                    processed_files.append((filename, has_audio))

    # Статистика обработки
    audio_files = sum(1 for _, has_audio in processed_files if has_audio)
    non_audio_files = sum(1 for _, has_audio in processed_files if not has_audio)
    
    logging.info("=" * 60)
    logging.info("СТАТИСТИКА ОБРАБОТКИ:")
    logging.info(f"Всего обработано: {len(processed_files)}")
    logging.info(f"Файлов с аудио: {audio_files}")
    logging.info(f"Файлов без аудио: {non_audio_files}")
    logging.info("=" * 60)
    
    if audio_files > 0:
        logging.info(f"✓ Аудиофайлы скопированы в: {internal_dir}")
    if non_audio_files > 0:
        logging.info(f"→ Неподдерживаемые файлы перемещены в: {garbage_dir}")

if __name__ == "__main__":
    main()