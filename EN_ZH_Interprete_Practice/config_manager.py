import json
import os
import base64


class ConfigManager:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> dict:
        """加载配置"""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            'extract_method': 'ffmpeg-command',
            'keep_video': False,
            'auto_transcribe': False,
            'transcribe_method': 'local',
            'whisper_model': 'base',
            'whisper_language': 'zh'  # 默认中文
        }

    def save_config(self):
        """保存配置"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    def get_api_key(self, masked: bool = False) -> str:
        """获取 API Key"""
        encoded_key = self.config.get('api_key', '')
        if not encoded_key:
            return ''

        try:
            key = base64.b64decode(encoded_key).decode('utf-8')
            if masked and len(key) > 8:
                return key[:4] + '*' * (len(key) - 8) + key[-4:]
            return key
        except:
            return ''

    def set_api_key(self, api_key: str):
        """设置 API Key（base64 简单混淆）"""
        if api_key:
            encoded = base64.b64encode(api_key.encode('utf-8')).decode('utf-8')
            self.config['api_key'] = encoded
        else:
            self.config['api_key'] = ''
        self.save_config()

    def get(self, key: str, default=None):
        """获取配置项"""
        return self.config.get(key, default)

    def set(self, key: str, value):
        """设置配置项"""
        self.config[key] = value
        self.save_config()

    def get_all(self) -> dict:
        """获取所有配置（隐藏敏感信息）"""
        result = self.config.copy()
        if 'api_key' in result:
            result['api_key_masked'] = self.get_api_key(masked=True)
            del result['api_key']
        return result