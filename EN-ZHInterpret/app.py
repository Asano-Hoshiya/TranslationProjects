from flask import Flask, render_template, request, jsonify, send_file
import os
import re
import shutil
from datetime import datetime
from database import Database
from config_manager import ConfigManager
from chatgpt_helper import ChatGPTHelper
from api_usage import APIUsageTracker
from utils import (
    split_text_by_sentences,
    is_video_file,
    is_audio_file,
    is_lrc_file,
    extract_audio_from_video,
    parse_lrc_file,
    merge_segments_by_duration,
    smart_split_with_ai,
    split_audio_segments
)
import json

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
for subdir in ['materials', 'recordings', 'splits']:
    os.makedirs(os.path.join(DATA_DIR, subdir), exist_ok=True)

db = Database(os.path.join(DATA_DIR, 'database.db'))
config = ConfigManager(os.path.join(DATA_DIR, 'config.json'))
usage_tracker = APIUsageTracker(os.path.join(DATA_DIR, 'api_usage.json'))


# ==================== 页面路由 ====================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/config')
def config_page():
    return render_template('config.html')


@app.route('/materials')
def materials_page():
    return render_template('materials.html')


@app.route('/practice/<int:material_id>')
def practice_page(material_id):
    return render_template('practice.html', material_id=material_id)


@app.route('/records')
def records_page():
    return render_template('records.html')


# ==================== API 路由 ====================

# 配置相关
# 配置相关
@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if request.method == 'GET':
        return jsonify(config.get_all())
    else:
        data = request.json

        if 'api_key' in data:
            config.set_api_key(data['api_key'])

        if 'extract_method' in data:
            config.set('extract_method', data['extract_method'])

        if 'keep_video' in data:
            config.set('keep_video', data['keep_video'])

        if 'auto_transcribe' in data:
            config.set('auto_transcribe', data['auto_transcribe'])

        if 'transcribe_method' in data:
            config.set('transcribe_method', data['transcribe_method'])

        if 'whisper_model' in data:
            config.set('whisper_model', data['whisper_model'])

        if 'whisper_language' in data:
            config.set('whisper_language', data['whisper_language'])

        if 'api_model' in data:
            config.set('api_model', data['api_model'])

        if 'api_rate_limit' in data:
            config.set('api_rate_limit', data['api_rate_limit'])

        if 'api_max_retries' in data:
            config.set('api_max_retries', data['api_max_retries'])

        return jsonify({'status': 'ok'})


# 素材管理
@app.route('/api/materials', methods=['GET', 'POST'])
def api_materials():
    if request.method == 'GET':
        materials = db.get_all_materials()
        return jsonify(materials)
    else:
        return handle_upload_material()


def handle_upload_material():
    """处理素材上传"""
    title = request.form.get('title')
    material_type = request.form.get('type', 'text')
    text_content = request.form.get('text', '')
    tags = request.form.get('tags', '')
    auto_transcribe = request.form.get('auto_transcribe', 'false') == 'true'

    audio_path = None

    # 处理 LRC 字幕文件
    if 'lrc' in request.files:
        lrc_file = request.files['lrc']
        if lrc_file.filename and is_lrc_file(lrc_file.filename):
            try:
                lrc_content = lrc_file.read().decode('utf-8')
                lrc_data = parse_lrc_file(lrc_content)

                text_content = lrc_data['text_with_timestamps']

                if 'audio' in request.files:
                    upload_res = handle_audio_upload(request.files['audio'])
                    # upload_res is dict: {'audio_path':..., 'video_path':...}
                    audio_path = upload_res.get('audio_path')
                    video_path = upload_res.get('video_path')

                material_id = db.add_material(title, material_type, text_content, audio_path, video_path, tags)

                split_file = os.path.join(DATA_DIR, 'splits', f'material_{material_id}.json')
                os.makedirs(os.path.dirname(split_file), exist_ok=True)
                with open(split_file, 'w', encoding='utf-8') as f:
                    json.dump(lrc_data['segments'], f, ensure_ascii=False, indent=2)

                db.update_material_split(material_id, f'splits/material_{material_id}.json')

                return jsonify({
                    'status': 'ok',
                    'id': material_id,
                    'lrc_imported': True,
                    'segments_count': len(lrc_data['segments'])
                })
            except Exception as e:
                return jsonify({'error': f'LRC 文件解析失败: {str(e)}'}), 400

    # 处理音频/视频文件
    if 'audio' in request.files:
        upload_res = handle_audio_upload(request.files['audio'])
        audio_path = upload_res.get('audio_path')
        video_path = upload_res.get('video_path')
        if audio_path is None:
            return jsonify({'error': '音频/视频处理失败'}), 500

    material_id = db.add_material(title, material_type, text_content, audio_path, video_path, tags)

    # 自动转写
    if auto_transcribe and audio_path:
        try:
            audio_full_path = os.path.join(DATA_DIR, audio_path)
            transcribed_text = transcribe_audio_file(audio_full_path)

            if transcribed_text:
                db.update_material_text(material_id, transcribed_text)

                # 如果转写结果包含时间戳，自动生成切分
                if re.search(r'\[\d{2}:\d{2}\.\d{2}\]', transcribed_text):
                    lrc_data = parse_lrc_file(transcribed_text)

                    split_file = os.path.join(DATA_DIR, 'splits', f'material_{material_id}.json')
                    os.makedirs(os.path.dirname(split_file), exist_ok=True)
                    with open(split_file, 'w', encoding='utf-8') as f:
                        json.dump(lrc_data['segments'], f, ensure_ascii=False, indent=2)

                    db.update_material_split(material_id, f'splits/material_{material_id}.json')

                return jsonify({
                    'status': 'ok',
                    'id': material_id,
                    'transcribed': True,
                    'text': transcribed_text,
                    'has_timestamps': True
                })
        except Exception as e:
            pass

    return jsonify({'status': 'ok', 'id': material_id})


def handle_audio_upload(media_file):
    """处理音频/视频文件上传"""
    if not media_file.filename:
        return None

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    original_filename = media_file.filename

    if is_video_file(original_filename):
        video_filename = f"{timestamp}_{original_filename}"
        video_path = os.path.join(DATA_DIR, 'materials', video_filename)
        media_file.save(video_path)

        audio_filename = f"{timestamp}_extracted.mp3"
        audio_full_path = os.path.join(DATA_DIR, 'materials', audio_filename)

        extract_method = config.get('extract_method', 'ffmpeg-command')
        success = extract_audio_from_video(video_path, audio_full_path, method=extract_method)

        if success:
            audio_path = os.path.join('materials', audio_filename)

            # By default keep uploaded video files. Only remove if keep_video is explicitly false
            if not config.get('keep_video', True):
                try:
                    os.remove(video_path)
                    video_rel = None
                except:
                    video_rel = os.path.join('materials', video_filename)
            else:
                video_rel = os.path.join('materials', video_filename)

            return {'audio_path': audio_path, 'video_path': video_rel}
        else:
            return None

    elif is_audio_file(original_filename):
        audio_filename = f"{timestamp}_{original_filename}"
        audio_path = os.path.join('materials', audio_filename)
        media_file.save(os.path.join(DATA_DIR, audio_path))
        return {'audio_path': audio_path, 'video_path': None}
    else:
        return {'audio_path': None, 'video_path': None}


# 获取单个素材
@app.route('/api/materials/<int:material_id>', methods=['GET', 'DELETE'])
def api_material(material_id):
    if request.method == 'GET':
        material = db.get_material(material_id)
        if not material:
            return jsonify({'error': 'Not found'}), 404
        # 尝试查找与 audio_path 关联的视频文件（同一时间戳前缀）
        try:
            audio_rel = material.get('audio_path')
            video_path = None
            if audio_rel:
                audio_basename = os.path.basename(audio_rel)
                m = re.match(r'^(\d{8}_\d{6})', audio_basename)
                if m:
                    prefix = m.group(1)
                    materials_dir = os.path.join(DATA_DIR, 'materials')
                    if os.path.exists(materials_dir):
                        for fname in os.listdir(materials_dir):
                            if fname.startswith(prefix) and is_video_file(fname):
                                video_path = os.path.join('materials', fname).replace('\\', '/')
                                break
            material['video_path'] = video_path
        except Exception:
            material['video_path'] = None

        return jsonify(material)
    else:
        return handle_delete_material(material_id)


def handle_delete_material(material_id):
    """删除素材"""
    material = db.get_material(material_id)
    if not material:
        return jsonify({'error': '素材不存在'}), 404

    files_to_delete = []
    dirs_to_delete = []

    if material['audio_path']:
        files_to_delete.append(os.path.join(DATA_DIR, material['audio_path']))

    if material['split_data_path']:
        files_to_delete.append(os.path.join(DATA_DIR, material['split_data_path']))

    segments_audio_dir = os.path.join(DATA_DIR, 'materials', f'segments_{material_id}')
    if os.path.exists(segments_audio_dir):
        dirs_to_delete.append(segments_audio_dir)

    success = db.delete_material(material_id)

    if success:
        for file_path in files_to_delete:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass

        for dir_path in dirs_to_delete:
            try:
                if os.path.exists(dir_path):
                    shutil.rmtree(dir_path)
            except:
                pass

        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': '删除失败'}), 500


# 智能切分
@app.route('/api/materials/<int:material_id>/smart-split', methods=['POST'])
def api_smart_split(material_id):
    material = db.get_material(material_id)
    if not material:
        return jsonify({'error': '素材不存在'}), 404

    if not material['text_content']:
        return jsonify({'error': '素材没有文本内容'}), 400

    try:
        data = request.json
        target_duration = float(data.get('duration', 30))
        use_ai = data.get('use_ai', False)

        text_content = material['text_content']

        if re.search(r'\[\d{2}:\d{2}\.\d{2}\]', text_content):
            lrc_data = parse_lrc_file(text_content)
            sentence_segments = lrc_data['segments']
        else:
            sentence_segments = split_text_by_sentences(text_content)

        if use_ai and config.get_api_key():
            try:
                helper = ChatGPTHelper(config.get_api_key())
                merged_segments = smart_split_with_ai(sentence_segments, target_duration, helper)
            except:
                merged_segments = merge_segments_by_duration(sentence_segments, target_duration)
        else:
            merged_segments = merge_segments_by_duration(sentence_segments, target_duration)

        audio_count = 0
        if material['audio_path']:
            audio_full_path = os.path.join(DATA_DIR, material['audio_path'])

            if os.path.exists(audio_full_path):
                segments_audio_dir = os.path.join(DATA_DIR, 'materials', f'segments_{material_id}')

                if os.path.exists(segments_audio_dir):
                    shutil.rmtree(segments_audio_dir)

                os.makedirs(segments_audio_dir, exist_ok=True)

                merged_segments = split_audio_segments(
                    audio_full_path,
                    merged_segments,
                    segments_audio_dir
                )

                for seg in merged_segments:
                    if 'audio_path' in seg:
                        seg['audio_path'] = f'materials/segments_{material_id}/{seg["audio_path"]}'
                        audio_count += 1

        split_file = os.path.join(DATA_DIR, 'splits', f'material_{material_id}.json')
        os.makedirs(os.path.dirname(split_file), exist_ok=True)
        with open(split_file, 'w', encoding='utf-8') as f:
            json.dump(merged_segments, f, ensure_ascii=False, indent=2)

        db.update_material_split(material_id, f'splits/material_{material_id}.json')

        return jsonify({
            'status': 'ok',
            'segments': merged_segments,
            'count': len(merged_segments),
            'audio_segments': audio_count,
            'method': 'ai' if use_ai else 'duration'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'切分失败: {str(e)}'}), 500


# 获取切分结果
@app.route('/api/materials/<int:material_id>/segments', methods=['GET'])
def api_segments(material_id):
    material = db.get_material(material_id)
    if not material or not material['split_data_path']:
        return jsonify([])

    split_file = os.path.join(DATA_DIR, material['split_data_path'])
    if os.path.exists(split_file):
        with open(split_file, 'r', encoding='utf-8') as f:
            segments = json.load(f)
        return jsonify(segments)

    return jsonify([])


@app.route('/api/materials/<int:material_id>/segments/generate-speeds', methods=['POST'])
def api_generate_speed_variants(material_id):
    """为素材的所有片段音频生成变速文件（0.5x/0.75x/1.25x/1.5x/2x），并在切分 JSON 中记录生成情况。"""
    material = db.get_material(material_id)
    if not material or not material.get('split_data_path'):
        return jsonify({'error': '素材或切分不存在'}), 404

    data = request.json or {}
    speeds = data.get('speeds', [0.5, 0.75, 1.25, 1.5, 2.0])

    # sanitize speeds
    try:
        speeds = sorted({float(s) for s in speeds})
    except Exception:
        speeds = [0.5, 0.75, 1.25, 1.5, 2.0]

    split_file = os.path.join(DATA_DIR, material['split_data_path'])
    if not os.path.exists(split_file):
        return jsonify({'error': '切分文件不存在'}), 404

    with open(split_file, 'r', encoding='utf-8') as f:
        segments = json.load(f)

    total = 0
    created = 0
    failed = 0

    for seg in segments:
        audio_rel = seg.get('audio_path')
        if not audio_rel:
            continue
        audio_full = os.path.join(DATA_DIR, audio_rel)
        if not os.path.exists(audio_full):
            continue

        total += 1
        # generate variants
        from utils import generate_speed_variants_for_segment
        res = generate_speed_variants_for_segment(audio_full, speeds)

        # attach result info to segment for later reference
        seg['audio_variants'] = {}
        for sp, ok in res.items():
            seg['audio_variants'][sp] = None
            if ok:
                # construct relative path for the generated file
                base = os.path.splitext(os.path.basename(audio_rel))[0]
                ext = os.path.splitext(audio_rel)[1]
                outname = f"{base}_{sp}x{ext}"
                seg['audio_variants'][sp] = os.path.join(os.path.dirname(audio_rel), outname).replace('\\', '/')
                created += 1
            else:
                failed += 1

    # write back updated segments to split file
    try:
        with open(split_file, 'w', encoding='utf-8') as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return jsonify({'status': 'ok', 'total_segments': total, 'created': created, 'failed': failed})


@app.route('/api/materials/<int:material_id>/analyses', methods=['GET'])
def api_material_analyses(material_id):
    """获取某素材关联的 AI 分析记录"""
    material = db.get_material(material_id)
    if not material:
        return jsonify({'error': '素材不存在'}), 404

    analyses = db.get_material_analyses(material_id)
    # 前端希望每种任务只显示最新的一条结果（summary/terms/difficulty 等）
    try:
        deduped = []
        seen_tasks = set()
        for a in analyses:
            t = a.get('task')
            if t and t not in seen_tasks:
                deduped.append(a)
                seen_tasks.add(t)
        return jsonify(deduped)
    except Exception:
        return jsonify(analyses)


# 转写音频
@app.route('/api/materials/<int:material_id>/transcribe', methods=['POST'])
def api_transcribe(material_id):
    material = db.get_material(material_id)
    if not material:
        return jsonify({'error': '素材不存在'}), 404

    if not material['audio_path']:
        return jsonify({'error': '该素材没有音频文件'}), 400

    try:
        audio_full_path = os.path.join(DATA_DIR, material['audio_path'])

        transcribed_text = transcribe_audio_file(audio_full_path)

        db.update_material_text(material_id, transcribed_text)

        # 如果转写结果包含时间戳，自动生成切分
        if re.search(r'\[\d{2}:\d{2}\.\d{2}\]', transcribed_text):
            lrc_data = parse_lrc_file(transcribed_text)

            split_file = os.path.join(DATA_DIR, 'splits', f'material_{material_id}.json')
            os.makedirs(os.path.dirname(split_file), exist_ok=True)
            with open(split_file, 'w', encoding='utf-8') as f:
                json.dump(lrc_data['segments'], f, ensure_ascii=False, indent=2)

            db.update_material_split(material_id, f'splits/material_{material_id}.json')

        return jsonify({
            'status': 'ok',
            'text': transcribed_text,
            'has_timestamps': True
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'转写失败: {str(e)}'}), 500


# ChatGPT 分析
# 在 ChatGPT 分析路由中记录
@app.route('/api/chatgpt/analyze', methods=['POST'])
def api_chatgpt_analyze():
    api_key = config.get_api_key()
    if not api_key:
        return jsonify({'error': 'API Key 未配置'}), 400

    data = request.json
    text = data.get('text', '')
    task = data.get('task', 'summary')

    # 读取可选配置以构造 helper
    rate_limit = config.get('api_rate_limit', 60)
    max_retries = config.get('api_max_retries', 4)
    api_model = config.get('api_model', 'gpt-4o-mini')

    helper = ChatGPTHelper(api_key, rate_limit_per_minute=rate_limit, max_retries=max_retries, default_model=api_model)

    try:
        if task == 'summary':
            result = helper.generate_summary(text)
        elif task == 'terms':
            result = helper.extract_terms(text)
        elif task == 'difficulty':
            result = helper.analyze_difficulty(text)
        else:
            result = {'error': '未知任务'}

        # 可选持久化：如果前端传入 material_id，则把分析结果与素材关联保存
        material_id = data.get('material_id')
        try:
            if material_id and (isinstance(material_id, int) or (isinstance(material_id, str) and str(material_id).isdigit())):
                mid = int(material_id)
                # pass the structured result directly; Database.add_material_analysis will persist JSON file
                db.add_material_analysis(mid, task, result)
        except Exception:
            pass

        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chatgpt/test', methods=['GET'])
def api_chatgpt_test():
    """测试 OpenAI API 连接和模型可用性。"""
    api_key = config.get_api_key()
    if not api_key:
        return jsonify({'ok': False, 'detail': 'API Key 未配置'}), 400

    rate_limit = config.get('api_rate_limit', 60)
    max_retries = config.get('api_max_retries', 6)
    api_model = config.get('api_model', 'gpt-3.5-turbo')

    helper = ChatGPTHelper(api_key, rate_limit_per_minute=rate_limit, max_retries=max_retries, default_model=api_model)
    result = helper.test_connection(check_model=api_model)
    status_code = 200 if result.get('ok') else 400
    return jsonify(result), status_code


@app.route('/api/chatgpt/models', methods=['GET'])
def api_chatgpt_models():
    """返回可用模型列表，便于前端下拉选择"""
    api_key = config.get_api_key()
    if not api_key:
        return jsonify({'ok': False, 'detail': 'API Key 未配置'}), 400

    rate_limit = config.get('api_rate_limit', 60)
    max_retries = config.get('api_max_retries', 6)
    helper = ChatGPTHelper(api_key, rate_limit_per_minute=rate_limit, max_retries=max_retries)
    try:
        result = helper.get_models()
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'detail': str(e)}), 500


# 练习记录
@app.route('/api/practice/save', methods=['POST'])
def api_save_practice():
    material_id = request.form.get('material_id')
    mode = request.form.get('mode', 'interpreting')

    recording_path = None
    if 'audio' in request.files:
        audio_file = request.files['audio']
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_practice.webm"
        recording_path = os.path.join('recordings', filename)
        audio_file.save(os.path.join(DATA_DIR, recording_path))

    record_id = db.add_practice_record(material_id, mode, recording_path)
    return jsonify({'status': 'ok', 'id': record_id})


@app.route('/api/records', methods=['GET'])
def api_get_records():
    records = db.get_practice_records()
    return jsonify(records)


@app.route('/api/records/<int:record_id>', methods=['DELETE'])
def api_delete_record(record_id):
    records = db.get_practice_records()
    record = next((r for r in records if r['id'] == record_id), None)

    if record and record['recording_path']:
        file_path = os.path.join(DATA_DIR, record['recording_path'])
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

    success = db.delete_practice_record(record_id)

    if success:
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': '删除失败'}), 500


# 静态文件服务
@app.route('/data/<path:filepath>')
def serve_data(filepath):
    return send_file(os.path.join(DATA_DIR, filepath))


# ==================== 辅助函数 ====================

def transcribe_audio_file(audio_path: str) -> str:
    """根据配置选择转写方法"""
    transcribe_method = config.get('transcribe_method', 'local')

    if transcribe_method == 'local':
        return transcribe_with_local_whisper(audio_path)
    elif transcribe_method == 'api':
        return transcribe_with_api(audio_path)
    else:
        raise ValueError(f"未知的转写方法: {transcribe_method}")


def transcribe_with_local_whisper(audio_path: str) -> str:
    """使用本地 Whisper 模型转写，返回带时间戳的 LRC 格式"""
    try:
        import whisper

        model_size = config.get('whisper_model', 'base')
        language = config.get('whisper_language', 'zh')

        print(f"[Whisper] 加载 {model_size} 模型，语言: {language}")
        model = whisper.load_model(model_size)

        print(f"[Whisper] 开始转写: {os.path.basename(audio_path)}")

        # 使用配置的语言进行转写
        # 如果设置为 'auto'，则自动检测语言
        transcribe_options = {
            'verbose': False,
            'word_timestamps': False
        }

        if language != 'auto':
            transcribe_options['language'] = language

        result = model.transcribe(audio_path, **transcribe_options)

        # 显示检测到的语言（如果是自动检测）
        detected_language = result.get('language', language)
        print(f"[Whisper] 检测到的语言: {detected_language}")

        print(f"[Whisper] 转写完成，开始生成 LRC 格式...")

        lrc_lines = []

        if 'segments' in result:
            for segment in result['segments']:
                start_time = segment.get('start', 0)
                text = segment.get('text', '').strip()

                if text:
                    minutes = int(start_time // 60)
                    seconds = int(start_time % 60)
                    centiseconds = int((start_time % 1) * 100)

                    lrc_line = f"[{minutes:02d}:{seconds:02d}.{centiseconds:02d}]{text}"
                    lrc_lines.append(lrc_line)

        lrc_text = '\n'.join(lrc_lines)

        print(f"[Whisper] 完成！生成 {len(lrc_lines)} 个带时间戳的片段")

        return lrc_text

    except ImportError:
        raise Exception("本地 Whisper 未安装，请运行: pip install openai-whisper")
    except Exception as e:
        raise Exception(f"本地 Whisper 转写失败: {str(e)}")


def transcribe_with_api(audio_path: str) -> str:
    """使用 OpenAI API 转写"""
    api_key = config.get_api_key()
    if not api_key:
        raise Exception("API Key 未配置")

    helper = ChatGPTHelper(api_key)
    return helper.transcribe_audio(audio_path)


if __name__ == '__main__':
    app.run(port=5000, host='0.0.0.0')