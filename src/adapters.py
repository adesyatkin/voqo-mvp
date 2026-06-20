import os
import sys
import base64
from pathlib import Path
import requests

# Загружаем .env с перезаписью
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=True)
except ImportError:
    pass

NVIDIA_API_KEYS = [k for k in os.environ.get("NVIDIA_API_KEYS", "").split(",") if k]
DEEPSEEK_KEYS = [k for k in os.environ.get("DEEPSEEK_API_KEYS", "").split(",") if k]

# Проверка gRPC
try:
    import grpc
    from riva.client.proto.riva_audio_pb2 import AudioEncoding
    from riva.client.proto.riva_asr_pb2 import RecognitionConfig, RecognizeRequest
    from riva.client.proto.riva_asr_pb2_grpc import RivaSpeechRecognitionStub
    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False

SERVER = "grpc.nvcf.nvidia.com:443"

def _transcribe_grpc(audio_path: str, api_key: str, function_id: str, language_code: str = "ru-RU") -> str:
    if not GRPC_AVAILABLE:
        raise RuntimeError("nvidia-riva-client не установлен. Выполните: pip install nvidia-riva-client")
    
    channel_credentials = grpc.ssl_channel_credentials()
    channel = grpc.secure_channel(SERVER, channel_credentials)
    stub = RivaSpeechRecognitionStub(channel)

    metadata = [
        ("authorization", f"Bearer {api_key}"),
        ("function-id", function_id),
    ]

    with open(audio_path, "rb") as f:
        audio_content = f.read()

    config = RecognitionConfig(
        encoding=AudioEncoding.LINEAR_PCM,
        sample_rate_hertz=16000,
        language_code=language_code,
        max_alternatives=1,
        audio_channel_count=1,
    )

    request = RecognizeRequest(config=config, audio=audio_content)
    response = stub.Recognize(request, metadata=metadata)

    transcripts = []
    for result in response.results:
        for alt in result.alternatives:
            transcripts.append(alt.transcript)
    return " ".join(transcripts)

class CanaryAdapter:
    def __init__(self):
        if len(NVIDIA_API_KEYS) < 1:
            raise ValueError("Нужен хотя бы один ключ для Canary")
        self.api_key = NVIDIA_API_KEYS[0]
        self.function_id = "b0e8b4a5-217c-40b7-9b96-17d84e666317"

    def transcribe(self, audio_path: str) -> str:
        return _transcribe_grpc(audio_path, self.api_key, self.function_id, language_code="ru-RU")

class WhisperAdapter:
    def __init__(self):
        if len(NVIDIA_API_KEYS) < 2:
            raise ValueError("Нужен второй ключ для Whisper")
        self.api_key = NVIDIA_API_KEYS[1]
        self.function_id = "b702f636-f60c-4a3d-a6f4-f3568c13bd7d"

    def transcribe(self, audio_path: str) -> str:
        return _transcribe_grpc(audio_path, self.api_key, self.function_id, language_code="ru")

class ParakeetAdapter:
    def __init__(self):
        if len(NVIDIA_API_KEYS) < 3:
            raise ValueError("Нужен третий ключ для Parakeet")
        self.api_key = NVIDIA_API_KEYS[2]
        self.function_id = "71203149-d3b7-4460-8231-1be2543a1fca"

    def transcribe(self, audio_path: str) -> str:
        return _transcribe_grpc(audio_path, self.api_key, self.function_id, language_code="multi")

class GemmaAdapter:
    """Gemma 3n E4B через REST."""
    def __init__(self):
        if len(NVIDIA_API_KEYS) < 4:
            raise ValueError("Нужен четвёртый ключ для Gemma")
        self.api_key = NVIDIA_API_KEYS[3]
        self.model = "google/gemma-3n-e4b-it"
        self.url = "https://integrate.api.nvidia.com/v1/chat/completions"

    def transcribe(self, audio_path: str) -> str:
        with open(audio_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_b64,
                                "format": "wav"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Transcribe the audio accurately in Russian. Output only the transcribed text."
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

        resp = requests.post(self.url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

class DeepSeekAdapter:
    def __init__(self):
        if not DEEPSEEK_KEYS:
            raise ValueError("DEEPSEEK_API_KEYS не задан")
        self.api_key = DEEPSEEK_KEYS[0]

    def correct(self, text: str) -> str:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=self.api_key
        )
        completion = client.chat.completions.create(
            model="deepseek-ai/deepseek-v4-flash",   # <-- Flash
            messages=[{"role": "user", "content": text}],
            temperature=0.2,
            max_tokens=200
        )
        return completion.choices[0].message.content.strip()