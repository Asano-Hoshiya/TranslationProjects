import re
import os
import subprocess
import time
from typing import List, Dict


def _ends_with_sentence_terminator(s: str) -> bool:
    """判断字符串是否以句子终止符结尾（考虑英文和中文及结尾引号）。"""
    if not s:
        return False
    stripped = s.rstrip().rstrip('"\'”’')
    return bool(re.search(r'[\.\!\?。！？]$', stripped))


def normalize_complete_sentences(segments: List[Dict]) -> List[Dict]:
    """将切分得到的片段合并，确保每个返回片段都是完整句子（以句子终止符结尾）。

    合并规则：从头开始累积片段文本，直到累积文本以句尾符号结束，才把该累计组作为一个输出片段。
    对于带时间戳的片段，会合并起始时间为首片段的 start_time，结束时间为最后片段的 end_time。
    """
    if not segments:
        return []

    out = []
    curr_texts = []
    curr_start = None
    curr_end = None

    def flush_group():
        if not curr_texts:
            return
        text = ' '.join(t for t in curr_texts if t)
        out.append({
            'index': len(out),
            'text': text,
            'start_time': curr_start,
            'end_time': curr_end
        })

    for seg in segments:
        txt = (seg.get('text') or '').strip()
        if curr_start is None:
            curr_start = seg.get('start_time')
        curr_end = seg.get('end_time')
        curr_texts.append(txt)

        # 如果当前累计以句子终止符结尾，则把它作为一个完整句子输出
        if _ends_with_sentence_terminator(txt):
            flush_group()
            curr_texts = []
            curr_start = None
            curr_end = None

    # 若有残余片段，也作为最后一段输出
    if curr_texts:
        flush_group()

    return out


def parse_lrc_file(lrc_content: str) -> Dict:
    """解析 LRC 字幕文件"""
    lines = lrc_content.strip().split('\n')
    segments = []
    full_text_lines = []

    lrc_pattern = re.compile(r'\[(\d{2}):(\d{2})\.?(\d{2})?\](.*)')

    for line in lines:
        match = lrc_pattern.match(line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            centiseconds = int(match.group(3) or 0)
            text = match.group(4).strip()

            if text:
                start_time = minutes * 60 + seconds + centiseconds / 100

                segments.append({
                    'index': len(segments),
                    'text': text,
                    'start_time': start_time,
                    'end_time': None
                })

                full_text_lines.append(text)

    for i in range(len(segments) - 1):
        segments[i]['end_time'] = segments[i + 1]['start_time']

    if segments:
        segments[-1]['end_time'] = None

    return {
        'text': '\n'.join(full_text_lines),
        'text_with_timestamps': '\n'.join([
            f"[{int(s['start_time'] // 60):02d}:{int(s['start_time'] % 60):02d}.{int((s['start_time'] % 1) * 100):02d}]{s['text']}"
            for s in segments
        ]),
        'segments': segments
    }


def merge_segments_by_duration(segments: List[Dict], target_duration: float = 30.0) -> List[Dict]:
    """按目标时长合并片段"""
    if not segments:
        return []

    has_timestamps = any(seg.get('start_time') is not None for seg in segments)

    if not has_timestamps:
        merged = []
        current_group = []

        for seg in segments:
            current_group.append(seg['text'])
            if len(current_group) >= 3:
                merged.append({
                    'index': len(merged),
                    'text': ' '.join(current_group),
                    'start_time': None,
                    'end_time': None
                })
                current_group = []

        if current_group:
            merged.append({
                'index': len(merged),
                'text': ' '.join(current_group),
                'start_time': None,
                'end_time': None
            })

        return merged

    merged = []
    current_group = []
    current_start = None

    for seg in segments:
        seg_start = seg.get('start_time')

        if seg_start is None:
            continue

        if current_start is None:
            current_start = seg_start
            current_group.append(seg['text'])
            continue

        potential_duration = seg_start - current_start

        if potential_duration <= target_duration:
            current_group.append(seg['text'])
        else:
            merged.append({
                'index': len(merged),
                'text': ' '.join(current_group),
                'start_time': current_start,
                'end_time': seg_start
            })

            current_group = [seg['text']]
            current_start = seg_start

    if current_group:
        last_end = None
        for seg in reversed(segments):
            if seg.get('end_time') is not None:
                last_end = seg['end_time']
                break

        merged.append({
            'index': len(merged),
            'text': ' '.join(current_group),
            'start_time': current_start,
            'end_time': last_end
        })

    return merged


def split_audio_segments(audio_path: str, segments: List[Dict], output_dir: str) -> List[Dict]:
    """使用 FFmpeg 切分音频"""
    if not os.path.exists(audio_path):
        return segments

    os.makedirs(output_dir, exist_ok=True)

    success_count = 0
    total_segments = len([s for s in segments if s.get('start_time') is not None])

    print(f"[音频切分] 开始切分 {total_segments} 个片段...")

    # timestamp to avoid overwriting files when re-splitting while files may be open
    base_time = int(time.time())

    for i, seg in enumerate(segments):
        start_time = seg.get('start_time')
        end_time = seg.get('end_time')

        if start_time is None:
            continue

        duration = None if end_time is None else end_time - start_time

        output_filename = f"segment_{i:03d}_{base_time}.mp3"
        output_path = os.path.join(output_dir, output_filename)

        # 修复格式化字符串
        end_time_str = f"{end_time:.1f}s" if end_time is not None else "结束"
        print(f"[音频切分] 处理片段 {success_count + 1}/{total_segments}: {start_time:.1f}s - {end_time_str}")

        cmd = ['ffmpeg', '-i', audio_path, '-ss', str(start_time)]

        if duration is not None:
            cmd.extend(['-t', str(duration)])

        cmd.extend(['-acodec', 'libmp3lame', '-b:a', '128k', '-y', output_path])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                segments[i]['audio_path'] = output_filename
                success_count += 1
        except:
            continue

    print(f"[音频切分] 完成！成功切分 {success_count}/{total_segments} 个片段")

    return segments


def generate_speed_variant(input_path: str, output_path: str, speed: float) -> bool:
    """使用 ffmpeg 的 atempo 过滤器为单个音频文件生成指定速度的变体。

    注意：ffmpeg 的 atempo 支持 0.5 到 2.0 范围内的速度。
    返回 True 表示生成成功并且文件存在且非空。
    """
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # atempo 支持 0.5-2.0，速度以浮点数形式传入
        cmd = [
            'ffmpeg', '-i', input_path,
            '-filter:a', f"atempo={speed}",
            '-acodec', 'libmp3lame', '-b:a', '128k', '-y', output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False


def generate_speed_variants_for_segment(segment_audio_fullpath: str, speeds: List[float]) -> Dict[str, bool]:
    """为给定片段音频生成多个速度变体。

    返回字典：{ '0.5': True, '0.75': False, ... } 表示每个速度是否生成成功。
    """
    results = {}
    if not os.path.exists(segment_audio_fullpath):
        return {str(s): False for s in speeds}

    base_dir = os.path.dirname(segment_audio_fullpath)
    base_name = os.path.splitext(os.path.basename(segment_audio_fullpath))[0]
    ext = os.path.splitext(segment_audio_fullpath)[1] or '.mp3'

    for s in speeds:
        # 不生成 1.0x，因为这是原文件
        if float(s) == 1.0:
            results[str(s)] = True
            continue

        out_name = f"{base_name}_{s}x{ext}"
        out_full = os.path.join(base_dir, out_name)
        ok = generate_speed_variant(segment_audio_fullpath, out_full, float(s))
        results[str(s)] = ok

    return results


def split_text_by_sentences(text: str) -> List[Dict]:
    """按句子切分文本"""
    sentences = re.split(r'([。！？\.\!\?]+)', text)

    segments = []
    current = ''

    for i, part in enumerate(sentences):
        current += part
        if re.match(r'[。！？\.\!\?]+', part):
            if current.strip():
                segments.append({
                    'index': len(segments),
                    'text': current.strip(),
                    'start_time': None,
                    'end_time': None
                })
            current = ''

    if current.strip():
        segments.append({
            'index': len(segments),
            'text': current.strip(),
            'start_time': None,
            'end_time': None
        })

    return segments


def smart_split_with_ai(segments: List[Dict], target_duration: float, api_helper) -> List[Dict]:
    """使用 AI 进行智能意群切分"""
    merged = merge_segments_by_duration(segments, target_duration)

    try:
        sample_segments = merged[:50] if len(merged) > 50 else merged
        # 为了让模型给出可解析的建议，要求返回严格的 JSON 数组，表示应当在这些索引之后断开分组
        text_list = [f"{i}. {seg['text'][:120].replace('\n', ' ')}" for i, seg in enumerate(sample_segments)]

        prompt = (
            "请根据下面的片段列表，判断哪些片段之间应当断开以形成更自然的意群（例如口译的切分）。"
            " 返回一个严格的 JSON 数组，数组内为要在其后断开的片段索引（整数），例如 [2,5,8] 表示在索引 2、5、8 之后各断一次。"
            " 不要输出任何解释性文字，纯粹返回 JSON。\n\n"
            "片段列表（格式：索引. 文本）:\n"
            + '\n'.join(text_list)
        )

        messages = [
            {'role': 'user', 'content': prompt}
        ]

        # 使用默认模型进行建议（成本较低）；如果调用成功且返回可解析的 JSON，则按建议分组
        ai_resp = api_helper.call_api(messages, model="gpt-5-nano", temperature=0.0)
        try:
            import json as _json
            breaks = _json.loads(ai_resp)
            if isinstance(breaks, list) and all(isinstance(x, int) for x in breaks):
                # apply breaks to the full merged list (not only the sample)
                break_set = set(breaks)
                new_groups = []
                curr_group = []
                curr_start = None
                for idx, seg in enumerate(merged):
                    if curr_start is None:
                        curr_start = seg.get('start_time')
                    curr_group.append(seg)
                    if idx in break_set:
                        # finalize current group
                        texts = [s.get('text','') for s in curr_group]
                        start_time = curr_group[0].get('start_time')
                        end_time = curr_group[-1].get('end_time')
                        new_groups.append({
                            'index': len(new_groups),
                            'text': ' '.join(texts),
                            'start_time': start_time,
                            'end_time': end_time
                        })
                        curr_group = []
                        curr_start = None
                # leftover
                if curr_group:
                    texts = [s.get('text','') for s in curr_group]
                    start_time = curr_group[0].get('start_time')
                    end_time = curr_group[-1].get('end_time')
                    new_groups.append({
                        'index': len(new_groups),
                        'text': ' '.join(texts),
                        'start_time': start_time,
                        'end_time': end_time
                    })
                return new_groups
        except Exception:
            # 如果解析失败，忽略 AI 建议，回退到基于时长的合并结果
            pass
    except Exception as e:
        print(f"[AI切分] 跳过AI优化: {e}")

    return merged


def extract_audio_from_video(video_path: str, output_path: str, method: str = 'ffmpeg-command') -> bool:
    """使用 FFmpeg 从视频提取音频"""
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vn',
            '-acodec', 'libmp3lame',
            '-b:a', '192k',
            '-y',
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        return result.returncode == 0
    except:
        return False


def get_file_extension(filename: str) -> str:
    """获取文件扩展名"""
    return os.path.splitext(filename)[1].lower()


def is_video_file(filename: str) -> bool:
    """判断是否为视频文件"""
    video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm', '.m4v']
    return get_file_extension(filename) in video_extensions


def is_audio_file(filename: str) -> bool:
    """判断是否为音频文件"""
    audio_extensions = ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma']
    return get_file_extension(filename) in audio_extensions


def is_lrc_file(filename: str) -> bool:
    """判断是否为 LRC 字幕文件"""
    return get_file_extension(filename) == '.lrc'


def format_duration(seconds: int) -> str:
    """格式化时长"""
    mins = seconds // 60
    secs = seconds % 60
    return f'{mins:02d}:{secs:02d}'


def format_time(seconds: float) -> str:
    """格式化时间戳"""
    if seconds is None:
        return '--:--'
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f'{mins:02d}:{secs:02d}'