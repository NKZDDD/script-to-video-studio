"""Use the model's authorized Key; pending tasks always use their original Key."""
from . import submitted
from .apiutil import ApiError, TASK_FATAL


def worker(pj, cfg, factory):
    choices = cfg['_key_candidates']

    def run(task, log, cancel):
        rec = submitted.load(pj.root, task['output'])
        selected = None
        if rec:
            old = rec.get('_usage') or {}
            for choice in choices:
                usage = choice['_usage']
                if old.get('key_id') and usage.get('key_id') != old['key_id']:
                    continue
                if old.get('key_hash') and usage.get('key_hash') != old['key_hash']:
                    continue
                selected = choice
                break
            if selected is None:
                raise ApiError('原任务的 Key 已被移除、停用或更换，请恢复原 Key 后取回；原任务 ID 已保留',
                               kind=TASK_FATAL, err_code='submitted_pending')
        else:
            selected = next((c for c in choices if c['_usage'] == cfg['_usage']), None)
        if selected is None:
            raise ApiError('当前模型没有对应的授权 Key，请刷新后台配置或联系管理员绑定模型', kind=TASK_FATAL)
        from .cloud_config import KEY_FIELDS
        # Runtime adds agent_mode/soften_rounds after resolving credentials. Keep
        # those execution options while pinning the selected Key and its address.
        selected = dict(selected)
        selected.update({k: v for k, v in cfg.items() if k not in
                         (*KEY_FIELDS, 'base_url', 'proxy', '_usage', 'assignment_id',
                          'key_id', 'key_name', '_key_candidates', 'models')})
        log('使用后台 Key：' + (selected['_usage'].get('key_name') or '未命名'))
        # No exception fallback: a different Key is not a retry policy.
        return factory(selected)(task, log, cancel)
    return run
