import requests
from typing import Dict, Optional, List
import os
import time
import random
import threading
import json
from requests.exceptions import RequestException


class ChatGPTHelper:
    """封装对 OpenAI Responses 与音频转写的调用：
    - 使用 `/v1/responses` 作为主聊天接口
    - 支持获取模型列表、测试连接、重试与简单速率限制
    """

    def __init__(self, api_key: str, base_url: str = 'https://api.openai.com/v1',
                 rate_limit_per_minute: int = 60, max_retries: int = 4, default_model: str = 'gpt-4o-mini'):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self._lock = threading.Lock()

        self.rate_limit_per_minute = max(1, int(rate_limit_per_minute))
        self._min_interval = 60.0 / float(self.rate_limit_per_minute)
        self._last_request_time = 0.0

        self.max_retries = max(1, int(max_retries))
        self.default_model = default_model

    def _wait_for_rate_limit(self):
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.time()

    def _request_with_retries(self, method: str, url: str, **kwargs) -> requests.Response:
        attempt = 0
        base_backoff = 0.5
        while True:
            attempt += 1
            try:
                self._wait_for_rate_limit()
                resp = self.session.request(method, url, timeout=kwargs.pop('timeout', 30), **kwargs)

                # Handle rate limit
                if resp.status_code == 429:
                    retry_after = resp.headers.get('Retry-After')
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except Exception:
                            wait = base_backoff * (2 ** (attempt - 1))
                    else:
                        wait = base_backoff * (2 ** (attempt - 1))

                    if attempt >= self.max_retries:
                        return resp
                    time.sleep(wait + random.uniform(0, 0.5))
                    continue

                # Retry on 5xx
                if 500 <= resp.status_code < 600:
                    if attempt >= self.max_retries:
                        return resp
                    wait = base_backoff * (2 ** (attempt - 1))
                    time.sleep(wait + random.uniform(0, 0.5))
                    continue

                return resp

            except RequestException:
                if attempt >= self.max_retries:
                    raise
                wait = base_backoff * (2 ** (attempt - 1))
                time.sleep(wait + random.uniform(0, 0.5))

    def get_models(self) -> Dict:
        headers = {'Authorization': f'Bearer {self.api_key}'}
        try:
            resp = self._request_with_retries('GET', f'{self.base_url}/models', headers=headers, timeout=10)
            if resp.status_code != 200:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                return {'ok': False, 'detail': f'status {resp.status_code}: {body}'}

            body = resp.json()
            models = [m.get('id') for m in body.get('data', []) if isinstance(m, dict)]
            return {'ok': True, 'models': models}
        except Exception as e:
            return {'ok': False, 'detail': str(e)}

    def test_connection(self, check_model: Optional[str] = None) -> Dict:
        headers = {'Authorization': f'Bearer {self.api_key}'}
        try:
            models_resp = self.get_models()
            if models_resp.get('ok'):
                models = models_resp.get('models') or []
                if check_model and check_model not in models:
                    return {'ok': False, 'detail': f"模型 '{check_model}' 不在模型列表中"}
                return {'ok': True, 'detail': 'models 可用'}
        except Exception:
            pass

        try:
            headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
            data = {'model': self.default_model, 'input': '测试连接'}
            resp = self._request_with_retries('POST', f'{self.base_url}/responses', headers=headers, json=data, timeout=10)
            if resp.status_code == 200:
                return {'ok': True, 'detail': 'responses 返回 200'}
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            return {'ok': False, 'detail': f'status {resp.status_code}: {body}'}
        except Exception as e:
            return {'ok': False, 'detail': str(e)}

    def call_api(self, messages: list, model: Optional[str] = None, temperature: float = 0.7) -> str:
        """
        新版 Responses API 调用
        Args:
            messages: [{"role":"user","content":"..."}] 列表
            model: 模型名称
            temperature: 生成温度
        Returns:
            模型文本输出
        """
        if not messages:
            raise ValueError('messages 不能为空')

        use_model = model or self.default_model
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}

        # Convert messages list to a single `input` string for Responses API
        if isinstance(messages, list):
            parts = []
            for m in messages:
                if isinstance(m, dict):
                    role = m.get('role')
                    content = m.get('content', '')
                    if role:
                        parts.append(f'[{role}] {content}')
                    else:
                        parts.append(str(content))
                else:
                    parts.append(str(m))
            input_payload = '\n'.join(parts)
        else:
            input_payload = str(messages)

        data = {'model': use_model, 'input': input_payload, 'temperature': temperature}

        resp = self._request_with_retries('POST', f'{self.base_url}/responses', headers=headers, json=data, timeout=30)

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise Exception(f'API 调用失败: status {resp.status_code}: {body}')

        try:
            body = resp.json()
        except Exception:
            return resp.text or ''

        # 解析 output_text 或 output 列表
        if 'output_text' in body:
            return body['output_text']

        out = body.get('output') or body.get('outputs')
        if isinstance(out, list) and out:
            texts = []
            for item in out:
                if isinstance(item, dict):
                    c = item.get('content')
                    if isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get('type') == 'output_text':
                                texts.append(b.get('text', ''))
                            elif isinstance(b, str):
                                texts.append(b)
                    elif isinstance(c, str):
                        texts.append(c)
                elif isinstance(item, str):
                    texts.append(item)
            if texts:
                return '\n'.join(texts)

        if isinstance(body.get('text'), str):
            return body.get('text')

        gens = body.get('generations') or body.get('generation')
        if isinstance(gens, list) and gens:
            first = gens[0]
            if isinstance(first, dict):
                if 'text' in first:
                    return first['text']
                if 'content' in first:
                    return str(first['content'])

        return ''

    def transcribe_audio(self, audio_path: str, model: str = 'whisper-1') -> str:
        """
        音频转文字（使用 Whisper API），返回 LRC 格式
        Args:
            audio_path: 音频文件路径
            model: Whisper 模型
        Returns:
            LRC 格式
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f'音频文件不存在: {audio_path}')

        if os.path.getsize(audio_path) > 25 * 1024 * 1024:
            raise ValueError('音频文件超过 25MB，请先压缩')

        headers = {'Authorization': f'Bearer {self.api_key}'}
        with open(audio_path, 'rb') as f:
            files = {'file': f}
            data = {'model': model, 'response_format': 'verbose_json', 'timestamp_granularities': ['segment']}
            resp = self._request_with_retries('POST', f'{self.base_url}/audio/transcriptions', headers=headers, files=files, data=data, timeout=120)

        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise Exception(f'Whisper API 调用失败: status {resp.status_code}: {body}')

        result = resp.json()
        if isinstance(result, dict) and 'segments' in result:
            return self._convert_to_lrc(result)
        return result.get('text', '')

    def _convert_to_lrc(self, whisper_result: dict) -> str:
        """将 Whisper 结果转换为 LRC 格式"""
        lines = []
        for seg in whisper_result.get('segments', []):
            start = seg.get('start', 0)
            text = seg.get('text', '').strip()
            if not text:
                continue
            minutes = int(start // 60)
            seconds = int(start % 60)
            centiseconds = int((start % 1) * 100)
            lines.append(f'[{minutes:02d}:{seconds:02d}.{centiseconds:02d}]{text}')
        return '\n'.join(lines)

    def generate_summary(self, text: str) -> Dict:
        """生成摘要"""
        if len(text) > 2000:
            text = text[:2000] + '...'
        messages = [{'role': 'user', 'content': f'请用中文生成100字以内摘要：\n\n{text}'}]
        try:
            return {'summary': self.call_api(messages, model=self.default_model, temperature=0.3)}
        except Exception as e:
            return {'error': f'摘要生成失败: {str(e)}'}

    def extract_terms(self, text: str) -> Dict:
        """提取术语"""
        if len(text) > 2000:
            text = text[:2000] + '...'
        # 尝试一次性让模型返回结构化 JSON：[{"term":"...","translation_zh":"..."}, ...]
        prompt = (
            "请从以下文本中提取5到10个关键术语，并为每个术语提供对应的中文翻译。"
            " 输出必须是严格的 JSON 列表，形如: [{\"term\": \"...\", \"translation_zh\": \"...\"}, ...]，不要额外的注释或说明。\n\n"
            f"文本：\n{text}"
        )
        messages = [{'role': 'user', 'content': prompt}]
        try:
            raw = self.call_api(messages, model=self.default_model, temperature=0.0)
            # 尝试解析 JSON
            try:
                parsed = json.loads(raw)
                # 确保是列表且每项包含 term 字段
                if isinstance(parsed, list):
                    cleaned: List[Dict] = []
                    for item in parsed:
                        if isinstance(item, dict):
                            term = item.get('term') or item.get('termin') or item.get('name')
                            zh = item.get('translation_zh') or item.get('zh') or item.get('translation')
                            if term:
                                cleaned.append({'term': str(term), 'translation_zh': str(zh) if zh else ''})
                    if cleaned:
                        return {'terms': cleaned}
            except Exception:
                # 解析失败，回退到文本分行形式
                pass

            # 如果上面的 JSON 解析失败，回退：将原始文本按行返回
            return {'terms': raw}
        except Exception as e:
            return {'error': f'术语提取失败: {str(e)}'}

    def analyze_difficulty(self, text: str) -> Dict:
        """分析难度"""
        if len(text) > 1000:
            text = text[:1000] + '...'
        messages = [{'role': 'user', 'content': f'分析口译难度（简单/中等/困难），并说明理由（50字内）：\n\n{text}'}]
        try:
            return {'difficulty': self.call_api(messages, model=self.default_model, temperature=0.3)}
        except Exception as e:
            return {'error': f'难度分析失败: {str(e)}'}
