import requests
import json

# 配置
URL = "http://localhost:18789/v1/chat/completions"
TOKEN = "admin123"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

# 盯盘指令
payload = {
    "model": "zai-org/GLM-latest",
    "messages": [
        {"role": "system", "content": "你是一个资深美股分析师。"},
        {"role": "user", "content": "获取 NVDA 和 TSLA 今天的实时行情，并分析它们的 MACD 金叉情况，给出明天开盘的买入建议。"}
    ]
}

try:
    response = requests.post(URL, headers=HEADERS, json=payload)
    if response.status_code == 200:
        print("--- 盯盘报告 ---")
        print(response.json()['choices'][0]['message']['content'])
    else:
        print(f"错误码: {response.status_code}, 内容: {response.text}")
except Exception as e:
    print(f"连接失败: {e}")