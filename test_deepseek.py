from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="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"
)

completion = client.chat.completions.create(
    model="deepseek-ai/deepseek-v4-pro",
    messages=[{"role": "user", "content": "Привет, как дела?"}],
    temperature=1,
    top_p=0.95,
    max_tokens=200,
    extra_body={"chat_template_kwargs": {"thinking": False}},
    stream=False
)

print(completion.choices[0].message.content)