#!/usr/bin/env python3
"""扫描 data/materials 目录，为数据库中已存在的素材补全 video_path 字段（如果有匹配的视频文件）。"""
import os
from database import Database

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'database.db')

def find_video_for_audio_basename(basename, materials_dir):
    # 尝试以时间戳前缀匹配文件名（格式: 20230101_123456_...）
    import re
    m = re.match(r'^(\d{8}_\d{6})', basename)
    if not m:
        return None
    prefix = m.group(1)
    for fname in os.listdir(materials_dir):
        if fname.startswith(prefix):
            # 简单判断是否为视频
            if os.path.splitext(fname)[1].lower() in ['.mp4', '.mkv', '.mov', '.avi', '.webm']:
                return os.path.join('materials', fname).replace('\\', '/')
    return None

def main():
    db = Database(DB_PATH)
    materials = db.get_all_materials()
    materials_dir = os.path.join(DATA_DIR, 'materials')
    updated = 0
    for m in materials:
        if m.get('video_path'):
            continue
        audio_rel = m.get('audio_path')
        if not audio_rel:
            continue
        basename = os.path.basename(audio_rel)
        vp = find_video_for_audio_basename(basename, materials_dir)
        if vp:
            # update material record
            try:
                conn = db.get_connection()
                cur = conn.cursor()
                cur.execute('UPDATE materials SET video_path = ? WHERE id = ?', (vp, m['id']))
                conn.commit()
                conn.close()
                updated += 1
                print(f"Updated material {m['id']} -> {vp}")
            except Exception as e:
                print('Failed to update', m['id'], e)

    print(f'Done. Updated {updated} materials.')

if __name__ == '__main__':
    main()
