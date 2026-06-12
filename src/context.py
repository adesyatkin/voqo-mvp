import os
import re
from typing import List, Dict, Tuple

class SimpleChunkMerger:
    def __init__(self, chunks_dir: str):
        self.chunks_dir = chunks_dir
        
    def extract_info_from_filename(self, filename: str) -> Tuple[str, int, int]:
        """Извлекает информацию из имени файла"""
        # Ищем паттерн: базовое_имя_whisperchank_начало-конец.txt
        pattern = r'(.+?)_whisperchank_(\d+)-(\d+)\.txt$'
        match = re.search(pattern, filename)
        if not match:
            raise ValueError(f"Некорректный формат имени файла: {filename}")
        
        base_name = match.group(1)
        start_time = int(match.group(2))
        end_time = int(match.group(3))
        
        return base_name, start_time, end_time
    
    def read_and_sort_chunks(self) -> Dict[str, List[Dict]]:
        """Читает все чанки и группирует их по базовому имени"""
        chunks_by_base = {}
        
        print("Сканирование файлов...")
        
        for filename in os.listdir(self.chunks_dir):
            if not filename.endswith('.txt'):
                continue
            
            # Пропускаем уже созданные объединенные файлы
            if 'объединенный' in filename:
                continue
                
            try:
                base_name, start_time, end_time = self.extract_info_from_filename(filename)
                
                filepath = os.path.join(self.chunks_dir, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    text = f.read().strip()
                
                chunk_data = {
                    'filename': filename,
                    'start_time': start_time,
                    'end_time': end_time,
                    'text': text
                }
                
                if base_name not in chunks_by_base:
                    chunks_by_base[base_name] = []
                chunks_by_base[base_name].append(chunk_data)
                
            except ValueError as e:
                print(f"Пропущен файл {filename}: не соответствует формату")
                continue
            except Exception as e:
                print(f"Ошибка при чтении {filename}: {e}")
                continue
        
        # Сортируем чанки каждого базового файла по начальному времени
        for base_name in chunks_by_base:
            chunks_by_base[base_name].sort(key=lambda x: x['start_time'])
            print(f"Найдено {len(chunks_by_base[base_name])} чанков для '{base_name}'")
        
        return chunks_by_base
    
    def save_merged_result(self, base_name: str, chunks: List[Dict]):
        """Сохраняет объединенный результат в файл"""
        # Формируем имя выходного файла
        output_filename = f"{base_name}__whisperchank_объединенный.txt"
        output_path = os.path.join(self.chunks_dir, output_filename)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            for chunk in chunks:
                # Форматируем временные метки (дополняем нулями до 9 цифр)
                start_formatted = f"{chunk['start_time']:09d}"
                end_formatted = f"{chunk['end_time']:09d}"
                
                # Записываем строку в требуемом формате
                f.write(f"[{start_formatted}-{end_formatted}] - {chunk['text']}\n")
        
        print(f"Создан файл: {output_filename}")
        print(f"Записано строк: {len(chunks)}")
    
    def process_all(self):
        """Основной метод обработки всех файлов"""
        print("=" * 60)
        print("Программа объединения чанков транскрипции")
        print(f"Папка с чанками: {self.chunks_dir}")
        print("=" * 60)
        
        # Читаем и группируем все чанки
        chunks_by_base = self.read_and_sort_chunks()
        
        if not chunks_by_base:
            print("Не найдено файлов для обработки.")
            print("Убедитесь, что:")
            print("1. Файлы находятся в правильной папке")
            print("2. Имена файлов соответствуют формату: имя_whisperchank_000000000-000025000.txt")
            return
        
        print(f"\nНайдено {len(chunks_by_base)} уникальных записей для обработки")
        
        # Обрабатываем каждую группу чанков
        for base_name, chunks in chunks_by_base.items():
            print(f"\n{'='*60}")
            print(f"Обработка записи: {base_name}")
            print(f"Количество чанков: {len(chunks)}")
            
            # Показываем диапазон времени для проверки
            if chunks:
                first_time = chunks[0]['start_time']
                last_time = chunks[-1]['end_time']
                print(f"Общий диапазон: {first_time:09d} - {last_time:09d}")
            
            # Сохраняем объединенный результат
            self.save_merged_result(base_name, chunks)
        
        print(f"\n{'='*60}")
        print("ОБРАБОТКА ЗАВЕРШЕНА!")
        print(f"Создано {len(chunks_by_base)} объединенных файлов")


def main():
    # Конфигурация - папка с чанками
    CHUNKS_DIR = r"D:\VOQO\workers\transcription_context"
    
    # Проверяем существование папки
    if not os.path.exists(CHUNKS_DIR):
        print(f"ОШИБКА: Папка не существует: {CHUNKS_DIR}")
        print("Создайте папку или укажите правильный путь.")
        return
    
    # Проверяем, есть ли файлы в папке
    txt_files = [f for f in os.listdir(CHUNKS_DIR) if f.endswith('.txt') and 'объединенный' not in f]
    if not txt_files:
        print(f"ОШИБКА: В папке {CHUNKS_DIR} нет файлов чанков.")
        print("Файлы должны иметь формат: имя_whisperchank_000000000-000025000.txt")
        return
    
    print(f"Найдено {len(txt_files)} файлов чанков для обработки")
    
    # Создаем и запускаем merger
    merger = SimpleChunkMerger(CHUNKS_DIR)
    merger.process_all()


if __name__ == "__main__":
    main()