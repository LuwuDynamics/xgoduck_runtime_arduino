"""Doubao clone TTS. Credentials stay in the board user's private config directory."""
import base64
import io
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid
import wave

ENDPOINT = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
RATE = 24000


class Cancelled(Exception):
    pass


def load_config():
    path = Path.home() / '.config/duck-device-test/doubao.json'
    if not path.exists():
        return {}
    config = json.loads(path.read_text())
    if not all(isinstance(config.get(k), str) and config[k].strip()
               for k in ('api_key', 'speaker', 'resource_id', 'model')):
        raise ValueError('豆包后端配置缺少必要参数。')
    return config


def synthesize(config, text, cancel, opener=urllib.request.urlopen):
    payload = {'req_params': {'text': text, 'speaker': config['speaker'],
        'model': config['model'], 'audio_params': {'format': 'pcm', 'sample_rate': RATE}}}
    request = urllib.request.Request(ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={'Content-Type': 'application/json', 'X-Api-Key': config['api_key'],
                 'X-Api-Resource-Id': config['resource_id'], 'X-Api-Request-Id': str(uuid.uuid4())})
    pcm = bytearray()
    complete = False
    started = time.monotonic()
    try:
        if cancel.is_set():
            raise Cancelled()
        with opener(request, timeout=15) as response:
            while True:
                if cancel.is_set():
                    raise Cancelled()
                if time.monotonic() - started > 60:
                    raise ValueError('语音合成超时，请稍后重试。')
                line = response.readline(4 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 4 * 1024 * 1024:
                    raise ValueError('语音服务响应过大。')
                if not line.strip():
                    continue
                item = json.loads(line)
                code = item.get('code')
                if code not in (0, 20000000):
                    raise ValueError(f"豆包返回 {code}：{item.get('message', '合成失败')}")
                if item.get('data'):
                    pcm.extend(base64.b64decode(item['data'], validate=True))
                if len(pcm) > RATE * 2 * 60:
                    raise ValueError('语音超过 60 秒，请缩短文本。')
                if code == 20000000:
                    complete = True
                    break
        if cancel.is_set():
            raise Cancelled()
        if not complete or not pcm or len(pcm) % 2:
            raise ValueError('合成音频不完整，请重试。')
    except Cancelled:
        raise
    except urllib.error.HTTPError as exc:
        # Never reflect provider bodies or credentials into the public webpage.
        labels = {401: 'API Key 无效', 403: '音色或模型权限不足', 429: '请求过多或额度不足'}
        raise ValueError(f'豆包 HTTP {exc.code}：{labels.get(exc.code, "服务请求失败")}。') from None
    except Exception as exc:
        raise ValueError(str(exc).replace(config['api_key'], '[redacted]')[:400]) from None
    result = io.BytesIO()
    with wave.open(result, 'wb') as w:
        w.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        w.writeframes(pcm)
    return result.getvalue()
