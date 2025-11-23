import sqlite3
import os
import time
import random
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # 定义 UTC+8 时区
        self.tz_utc8 = timezone(timedelta(hours=8))
        self.init_database()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_current_time(self) -> str:
        """获取当前 UTC+8 时间"""
        return datetime.now(self.tz_utc8).strftime('%Y-%m-%d %H:%M:%S')

    def init_database(self):
        """初始化数据库表"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # 素材表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                text_content TEXT,
                audio_path TEXT,
                video_path TEXT,
                split_data_path TEXT,
                tags TEXT,
                created_at TEXT NOT NULL
            )
        ''')

        # 练习记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS practice_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER,
                mode TEXT,
                recording_path TEXT,
                duration INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (material_id) REFERENCES materials (id)
            )
        ''')

        # 素材 AI 分析表（存放每次 AI 生成的结果）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS material_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_id INTEGER,
                task TEXT,
                result TEXT,
                result_path TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (material_id) REFERENCES materials (id)
            )
        ''')
        # Ensure column `result_path` exists (for older DBs)
        try:
            cursor.execute("PRAGMA table_info(material_analyses)")
            cols = [r[1] for r in cursor.fetchall()]
            if 'result_path' not in cols:
                cursor.execute("ALTER TABLE material_analyses ADD COLUMN result_path TEXT")
        except Exception:
            pass

        conn.commit()
        conn.close()

    def add_material(self, title: str, material_type: str, text_content: str = '',
                     audio_path: str = None, video_path: str = None, tags: str = '') -> int:
        """添加素材"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (title, type, text_content, audio_path, video_path, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (title, material_type, text_content, audio_path, video_path, tags, self.get_current_time()))
        material_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return material_id

    def get_all_materials(self) -> List[Dict]:
        """获取所有素材"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM materials ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_material(self, material_id: int) -> Optional[Dict]:
        """获取单个素材"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM materials WHERE id = ?', (material_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_material_split(self, material_id: int, split_path: str):
        """更新素材切分数据路径"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE materials SET split_data_path = ? WHERE id = ?',
                       (split_path, material_id))
        conn.commit()
        conn.close()

    def update_material_text(self, material_id: int, text_content: str):
        """更新素材文本内容"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE materials SET text_content = ? WHERE id = ?',
                       (text_content, material_id))
        conn.commit()
        conn.close()

    def delete_material(self, material_id: int) -> bool:
        """删除素材"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM practice_records WHERE material_id = ?', (material_id,))
        cursor.execute('DELETE FROM materials WHERE id = ?', (material_id,))

        affected = cursor.rowcount
        conn.commit()
        conn.close()

        return affected > 0

    def add_practice_record(self, material_id: int, mode: str,
                            recording_path: str = None) -> int:
        """添加练习记录"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO practice_records (material_id, mode, recording_path, created_at)
            VALUES (?, ?, ?, ?)
        ''', (material_id, mode, recording_path, self.get_current_time()))
        record_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return record_id

    def add_material_analysis(self, material_id: int, task: str, result: str) -> int:
        """保存与素材关联的 AI 分析结果（result 存为文本 JSON 或纯文本）"""
        # If result is a dict/list, serialize to JSON file under data/analyses and store path
        conn = self.get_connection()
        cursor = conn.cursor()
        result_path = None
        try:
            if isinstance(result, (dict, list)):
                data_dir = os.path.dirname(self.db_path)
                analyses_dir = os.path.join(data_dir, 'analyses')
                os.makedirs(analyses_dir, exist_ok=True)
                filename = f"material_{material_id}_analysis_{int(time.time())}_{random.randint(1000,9999)}.json"
                fullpath = os.path.join(analyses_dir, filename)
                with open(fullpath, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                # store relative path
                result_path = os.path.join('analyses', filename)
                # store a short preview in result text column: prefer 'summary' if available
                if isinstance(result, dict) and 'summary' in result:
                    preview = result.get('summary')
                else:
                    # fallback to full JSON string (might be large)
                    preview = json.dumps(result, ensure_ascii=False)
                result_text = preview
            else:
                result_text = str(result)
        except Exception:
            result_text = str(result)

        cursor.execute('''
            INSERT INTO material_analyses (material_id, task, result, result_path, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (material_id, task, result_text, result_path, self.get_current_time()))
        aid = cursor.lastrowid
        conn.commit()
        conn.close()
        return aid

    def get_material_analyses(self, material_id: int) -> List[Dict]:
        """获取某个素材的所有 AI 分析记录，按时间倒序"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM material_analyses WHERE material_id = ? ORDER BY created_at DESC
        ''', (material_id,))
        rows = cursor.fetchall()
        conn.close()
        results = []
        for row in rows:
            rec = dict(row)
            # if there's a result_path, try to load the JSON file
            rp = rec.get('result_path')
            if rp:
                try:
                    data_dir = os.path.dirname(self.db_path)
                    full = os.path.join(data_dir, rp)
                    if os.path.exists(full):
                        with open(full, 'r', encoding='utf-8') as f:
                            rec['result'] = json.load(f)
                except Exception:
                    # fall back to text in DB
                    pass
            else:
                # try to parse JSON stored in result text
                try:
                    parsed = json.loads(rec.get('result') or 'null')
                    rec['result'] = parsed
                except Exception:
                    # leave as string
                    pass

            results.append(rec)

        return results

    def get_practice_records(self, limit: int = 50) -> List[Dict]:
        """获取练习记录"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT pr.*, m.title as material_title
            FROM practice_records pr
            LEFT JOIN materials m ON pr.material_id = m.id
            ORDER BY pr.created_at DESC
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def delete_practice_record(self, record_id: int) -> bool:
        """删除练习记录"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM practice_records WHERE id = ?', (record_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0