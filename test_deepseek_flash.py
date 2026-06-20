import time
from openai import OpenAI

API_KEY = "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"

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=API_KEY
)

print("Тест DeepSeek V4 Flash...")
start = time.time()
try:
    completion = client.chat.completions.create(
        model="deepseek-ai/deepseek-v4-flash",
        messages=[{"role": "user", "content": "Исправь ошибки: Привет, как дела?"}],
        temperature=0.2,
        max_tokens=200,
        timeout=15
    )
    elapsed = time.time() - start
    print(f"OK ({elapsed:.1f}s): {completion.choices[0].message.content}")
except Exception as e:
    elapsed = time.time() - start
    print(f"FAIL ({elapsed:.1f}s): {e}")