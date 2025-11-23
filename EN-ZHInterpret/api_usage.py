import json
import os
from datetime import datetime, timedelta, timezone


class APIUsageTracker:
    """API 调用统计"""

    def __init__(self, usage_file: str):
        self.usage_file = usage_file
        self.tz_utc8 = timezone(timedelta(hours=8))
        self.usage_data = self.load_usage()

    def load_usage(self) -> dict:
        """加载使用记录"""
        if os.path.exists(self.usage_file):
            with open(self.usage_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {'calls': [], 'total': 0}

    def save_usage(self):
        """保存使用记录"""
        with open(self.usage_file, 'w', encoding='utf-8') as f:
            json.dump(self.usage_data, f, indent=2, ensure_ascii=False)

    def record_call(self, api_type: str, model: str, estimated_cost: float = 0.0):
        """记录一次调用"""
        call_record = {
            'timestamp': datetime.now(self.tz_utc8).strftime('%Y-%m-%d %H:%M:%S'),
            'api_type': api_type,
            'model': model,
            'estimated_cost': estimated_cost
        }

        self.usage_data['calls'].append(call_record)
        self.usage_data['total'] += 1

        # 只保留最近 100 条记录
        if len(self.usage_data['calls']) > 100:
            self.usage_data['calls'] = self.usage_data['calls'][-100:]

        self.save_usage()

    def get_today_stats(self) -> dict:
        """获取今日统计"""
        today = datetime.now(self.tz_utc8).strftime('%Y-%m-%d')
        today_calls = [c for c in self.usage_data['calls'] if c['timestamp'].startswith(today)]

        return {
            'count': len(today_calls),
            'estimated_cost': sum(c.get('estimated_cost', 0) for c in today_calls)
        }