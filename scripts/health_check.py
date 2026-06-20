import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from adapters import CanaryAdapter, WhisperAdapter, ParakeetAdapter, GemmaAdapter, DeepSeekAdapter

LOG_FILE = Path(__file__).parent.parent / "logs" / "health_check.log"
LOG_FILE.parent.mkdir(exist_ok=True)

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def check_asr(name, adapter, audio_path):
    start = time.time()
    try:
        text = adapter.transcribe(audio_path)
        elapsed = time.time() - start
        if text and len(text) > 1:
            log(f"{name}: OK ({elapsed:.1f}s) - {text[:50]}...")
        else:
            log(f"{name}: FAIL - пустой ответ")
    except Exception as e:
        elapsed = time.time() - start
        log(f"{name}: FAIL ({elapsed:.1f}s) - {e}")

def check_llm(name, adapter):
    start = time.time()
    try:
        result = adapter.correct("Привет, как дела?")
        elapsed = time.time() - start
        if result and len(result) > 1:
            log(f"{name}: OK ({elapsed:.1f}s) - {result[:50]}...")
        else:
            log(f"{name}: FAIL - пустой ответ")
    except Exception as e:
        elapsed = time.time() - start
        log(f"{name}: FAIL ({elapsed:.1f}s) - {e}")

def main():
    log("===== Health Check Start =====")
    test_audio = Path(__file__).parent.parent / "data" / "test" / "test_audio.wav"

    try:
        canary = CanaryAdapter()
        check_asr("Canary", canary, str(test_audio))
    except Exception as e:
        log(f"Canary: INIT FAIL - {e}")

    try:
        whisper = WhisperAdapter()
        check_asr("Whisper", whisper, str(test_audio))
    except Exception as e:
        log(f"Whisper: INIT FAIL - {e}")

    try:
        parakeet = ParakeetAdapter()
        check_asr("Parakeet", parakeet, str(test_audio))
    except Exception as e:
        log(f"Parakeet: INIT FAIL - {e}")

    try:
        gemma = GemmaAdapter()
        check_asr("Gemma E4B", gemma, str(test_audio))
    except Exception as e:
        log(f"Gemma: INIT FAIL - {e}")

    try:
        deepseek = DeepSeekAdapter()
        check_llm("DeepSeek V4 Flash", deepseek)   # <-- Flash
    except Exception as e:
        log(f"DeepSeek: INIT FAIL - {e}")

    log("===== Health Check End =====\n")

if __name__ == "__main__":
    main()