#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cleanup_folders.py - Очистка указанных папок (удаление всех файлов внутри, папки остаются)
"""

import os
import shutil
from pathlib import Path

def clear_folder(folder_path: Path, dry_run: bool = False):
    """Удаляет все файлы и подпапки внутри указанной папки, но саму папку оставляет."""
    if not folder_path.exists():
        print(f"⚠️ Папка не существует: {folder_path}")
        return

    if not folder_path.is_dir():
        print(f"⚠️ Указанный путь не является папкой: {folder_path}")
        return

    items = list(folder_path.iterdir())
    if not items:
        print(f"   Папка уже пуста: {folder_path}")
        return

    for item in items:
        if dry_run:
            print(f"   [DRY RUN] Удалить: {item}")
        else:
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                print(f"   Удалён: {item}")
            except Exception as e:
                print(f"   ❌ Ошибка при удалении {item}: {e}")

def main():
    # Определяем базовые пути
    workers_dir = Path("D:/VOQO/workers")
    cleaner_dir = Path("D:/VOQO/cleaner")   # или где находится ваш cleaner

    # Список папок для очистки (относительные пути от корня проекта)
    folders_to_clean = [
        workers_dir / "transcription_whisper",
        workers_dir / "transcription_context",
        workers_dir / "transcription_canary",
        workers_dir / "context_canary",
        workers_dir / "context_gemma",
        cleaner_dir / "chunk_files",
    ]

    print("=" * 60)
    print("ОЧИСТКА ПАПОК")
    print("=" * 60)

    # Опция dry run (показывать что будет удалено, но не удалять)
    dry_run = input("Выполнить пробный прогон (dry run) без реального удаления? (y/n): ").strip().lower() == 'y'

    for folder in folders_to_clean:
        print(f"\nОчистка: {folder}")
        clear_folder(folder, dry_run=dry_run)

    print("\n" + "=" * 60)
    if dry_run:
        print("Пробный прогон завершён. Файлы не были удалены.")
    else:
        print("Очистка завершена.")
    print("=" * 60)

if __name__ == "__main__":
    main()