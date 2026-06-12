#!/usr/bin/env python3
# main.py - последовательный запуск полного пайплайна для всех файлов

import os
import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('Main')

env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
env['PYTHONUTF8'] = '1'

async def run_script(script_name: str, capture: bool = False) -> bool:
    logger.info(f"🚀 Запуск {script_name}")
    if capture:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode('utf-8', errors='replace')
        err = stderr.decode('utf-8', errors='replace')
        if out:
            logger.info(f"STDOUT {script_name}:\n{out}")
        if err:
            logger.error(f"STDERR {script_name}:\n{err}")
    else:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_name,
            env=env
        )
        await proc.wait()
    if proc.returncode != 0:
        logger.error(f"❌ {script_name} завершился с ошибкой {proc.returncode}")
        return False
    logger.info(f"✅ {script_name} успешно завершён")
    return True

async def run_processor():
    return await run_script("processor.py", capture=True)

async def run_transcriptions():
    logger.info("🎤 Запуск транскрипционных модулей параллельно")
    modules = [
        ("transcription_canary.py",   "Canary"),
        ("transcription_gemma.py",    "Gemma"),      # заменён Parakeet
        ("transcription_whisper.py",  "Whisper"),
        ("transcription_context.py",  "Context")
    ]
    procs = []
    for script, name in modules:
        logger.info(f"  Запуск {name}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script,
            env=env
        )
        procs.append(proc)

    results = await asyncio.gather(*(p.wait() for p in procs), return_exceptions=True)
    all_ok = True
    for i, (script, name) in enumerate(modules):
        if isinstance(results[i], Exception) or results[i] != 0:
            logger.error(f"❌ {name} завершился с ошибкой: {results[i]}")
            all_ok = False
        else:
            logger.info(f"✅ {name} завершён успешно")
    return all_ok

async def run_merging():
    return await run_script("merging.py")

async def run_corrector():
    return await run_script("corrector.py")

async def main():
    logger.info("=" * 70)
    logger.info("ЗАПУСК ПОЛНОГО ПАЙПЛАЙНА ТРАНСКРИПЦИИ")
    logger.info("=" * 70)

    start_time = datetime.now()

    if not await run_processor():
        logger.error("Остановка из-за ошибки в processor")
        return 1

    if not await run_transcriptions():
        logger.warning("⚠️ Некоторые транскрипции завершились с ошибками, но продолжаем")

    if not await run_merging():
        logger.warning("⚠️ Ошибка при merging, но продолжаем")

    if not await run_corrector():
        logger.warning("⚠️ Ошибка при corrector")

    elapsed = datetime.now() - start_time
    logger.info("=" * 70)
    logger.info(f"✅ ВСЕ ЭТАПЫ ЗАВЕРШЕНЫ. Общее время: {elapsed}")
    logger.info("=" * 70)
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)