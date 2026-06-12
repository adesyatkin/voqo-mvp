#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Launcher для запуска всей системы транскрибации
Запускайте этот файл двойным кликом мыши
"""

import os
import sys
import subprocess
import time
import logging
from datetime import datetime

def setup_launcher_logger():
    """Настройка логгера для лаунчера"""
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"launcher_{timestamp}.log")
    
    logger = logging.getLogger('Launcher')
    logger.setLevel(logging.INFO)
    
    # Очищаем старые обработчики
    logger.handlers.clear()
    
    # Форматтер
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Файловый обработчик
    file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Консольный обработчик
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger, log_file

def setup_environment():
    """Настройка окружения для корректного запуска"""
    # Устанавливаем кодировку для Windows
    if sys.platform == "win32":
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        os.environ['PYTHONUTF8'] = '1'
    
    # Устанавливаем текущую директорию как директорию скрипта
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

def check_python():
    """Проверка версии Python и наличия зависимостей"""
    try:
        import platform
        python_version = platform.python_version()
        logger.info(f"Python версия: {python_version}")
        
        if sys.version_info < (3, 7):
            logger.error("❌ Требуется Python 3.7 или выше!")
            return False
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка проверки Python: {e}")
        return False

def run_main():
    """Запуск основного скрипта"""
    try:
        setup_environment()
        
        logger.info("=" * 70)
        logger.info("🚀 ЗАПУСК СИСТЕМЫ ТРАНСКРИБАЦИИ")
        logger.info("=" * 70)
        
        if not check_python():
            logger.error("Python проверка не пройдена")
            return 1
        
        logger.info("\n📋 Проверка зависимостей...")
        
        # Проверяем наличие необходимых файлов
        required_files = [
            "main.py",
            "distributor.py", 
            "converter.py",
            "separator.py",
            "chunking.py",
            "transcription_canary.py",
            "transcription_parakeet.py",
            "transcription_whisper.py",
            "merging.py",
            "config_canary.py",
            "config_parakeet.py",
            "config_whisper.py"
        ]
        
        missing_files = []
        for file in required_files:
            if not os.path.exists(file):
                missing_files.append(file)
        
        if missing_files:
            logger.error("❌ Отсутствуют необходимые файлы:")
            for file in missing_files:
                logger.error(f"  - {file}")
            return 1
        
        logger.info("✅ Все необходимые файлы найдены")
        
        # Проверяем папку input_files
        input_files_path = os.path.join(os.path.dirname(__file__), "input_files")
        if not os.path.exists(input_files_path):
            os.makedirs(input_files_path)
            logger.info("📁 Создана папка input_files")
        
        input_files = [f for f in os.listdir(input_files_path) if os.path.isfile(os.path.join(input_files_path, f))]
        
        if not input_files:
            logger.warning("\n⚠️  ВНИМАНИЕ: Папка input_files пуста!")
            logger.info("Поместите файлы для транскрибации в папку input_files")
            logger.info("и запустите программу снова.")
            return 1
        
        logger.info(f"\n📁 Найдено файлов для обработки: {len(input_files)}")
        for i, file in enumerate(input_files[:10], 1):
            logger.info(f"  {i}. {file}")
        if len(input_files) > 10:
            logger.info(f"  ... и еще {len(input_files) - 10} файлов")
        
        logger.info("\n" + "=" * 70)
        logger.info("⚡ ЗАПУСК ПРОЦЕССА ТРАНСКРИБАЦИИ")
        logger.info("=" * 70)
        logger.info("\n📝 Примечание:")
        logger.info("• Процесс может занять значительное время")
        logger.info("• Не закрывайте окно до завершения")
        logger.info("• Подробные логи будут сохранены в папке logs/")
        logger.info("• Для прерывания нажмите Ctrl+C")
        logger.info(f"• Лог лаунчера: {log_file}")
        
        time.sleep(2)
        
        # Запускаем main.py
        logger.info("\n" + "=" * 70)
        logger.info("🚀 ЗАПУСК ОСНОВНОГО ПРОЦЕССА...")
        logger.info("=" * 70 + "\n")
        
        # Запускаем через subprocess с правильными настройками
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        
        process = subprocess.Popen(
            [sys.executable, "main.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            env=env
        )
        
        # Записываем вывод main.py в лог лаунчера
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                logger.info(f"[MAIN.PY] {line.strip()}")
        
        process.wait()
        
        logger.info("\n" + "=" * 70)
        logger.info("🏁 ПРОЦЕСС ЗАВЕРШЕН")
        logger.info("=" * 70)
        
        # Проверяем наличие результатов
        results_found = False
        for folder in ["transcription_canary", "transcription_parakeet", "transcription_whisper", "merged_results"]:
            if os.path.exists(folder):
                files = [f for f in os.listdir(folder) if f.endswith('.txt')]
                if files:
                    results_found = True
                    logger.info(f"\n📁 {folder}: {len(files)} файлов")
        
        if results_found:
            logger.info("\n✅ Результаты транскрибации сохранены в соответствующих папках")
        else:
            logger.warning("\n⚠️  Результаты транскрибации не найдены")
            logger.info("Проверьте логи в папке logs/ для выяснения причины")
        
        logger.info("\n💾 Логи сохранены в папке 'logs':")
        log_files = [f for f in os.listdir("logs") if f.endswith('.log')]
        for log_file in log_files[:5]:
            logger.info(f"  • {log_file}")
        if len(log_files) > 5:
            logger.info(f"  ... и еще {len(log_files) - 5} файлов")
        
        return process.returncode
        
    except KeyboardInterrupt:
        logger.warning("\n\n❌ Процесс прерван пользователем")
        return 1
    except Exception as e:
        logger.error(f"\n❌ Критическая ошибка: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 1

if __name__ == "__main__":
    # Инициализация логгера
    logger, log_file = setup_launcher_logger()
    
    try:
        exit_code = run_main()
        logger.info(f"\nЗавершение работы лаунчера с кодом: {exit_code}")
        sys.exit(exit_code)
    except Exception as e:
        logger.critical(f"Фатальная ошибка лаунчера: {e}")
        sys.exit(2)