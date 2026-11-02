"""Requested output duration is not constrained by cached capability hints."""
import re


def positive_seconds(value):
    try:
        seconds = int(value)
        if isinstance(value, bool) or seconds <= 0:
            raise ValueError
        if isinstance(value, str):
            if not re.fullmatch(r'[0-9]+', value.strip()):
                raise ValueError
        elif value != seconds:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('视频秒数须为正整数；留空可跟随任务') from exc
    return seconds


def protocol_note(cap, model):
    video = cap.get('video') or {}
    effective = dict(video, **(video.get('model_options', {}).get(model) or {}))
    if effective.get('duration_mode') == 'fixed':
        return f"该接口固定 {effective['fixed_duration']} 秒，不发送任务秒数；需要其他时长请换模型或服务商"
    if effective.get('duration_mode') == 'model':
        return '时长由模型 / 服务商决定，接口不接收秒数参数；需要其他时长请选择对应模型'
    return ''


def validate_override(value, cap, model):
    seconds = positive_seconds(value)
    video = cap.get('video') or {}
    effective = dict(video, **(video.get('model_options', {}).get(model) or {}))
    if (effective.get('duration_mode') == 'model' or
            effective.get('duration_mode') == 'fixed' and seconds != effective.get('fixed_duration')):
        raise ValueError(protocol_note(cap, model) + '；请清空旧的秒数覆盖')
    return seconds
