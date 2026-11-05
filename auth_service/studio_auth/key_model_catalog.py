"""Bounded per-Key model discovery using inert adapter rules and safe GET probes."""
import copy
import queue
import re
import threading
import time

from .monitor_catalog import resolve, rule_digest
from .provider_probe import probe


def fetch(rule, cfg, reader=probe):
    if rule.get('kind') != 'models' or rule.get('auth') == 'none':
        return dict(state='unsupported', models=[], message='此接口不能确认 Key 的模型范围，请在 Key 库绑定模型')
    # A legacy Key entry may hold multiple capability credentials. Read each,
    # without treating the monitoring rule's first credential as the entire Key.
    values = []
    for field in rule.get('key_fields', ['api_key']):
        value = str(cfg.get(field) or '').strip()
        if field == 'api_key' and rule.get('key_prefixes') and '=' in value:
            values += [part.partition('=')[2].strip() for part in re.split(r'[;\n]+', value)
                       if part.partition('=')[0].strip().lower() in rule['key_prefixes']]
        elif value:
            values.append(value)
    values = list(dict.fromkeys(v for v in values if v))
    if not values or len(values) > 8:
        return dict(state='error', models=[], message='请检查此 Key 的模型读取凭据')
    models = []
    for value in values:
        spec, secret = resolve(dict(rule, key_fields=['api_key'], key_prefixes=[]),
                               dict(base_url=cfg.get('base_url', ''), api_key=value))
        result = reader(spec, secret, model_id_field='id')
        if result['category'] != 'ok':
            # Do not misrepresent a partial catalog as all models accessible to a Key.
            label = {'authentication': '鉴权失败', 'timeout': '连接超时',
                     'rate_limited': '请求过于频繁'}.get(result['category'], '读取失败')
            return dict(state='error', models=[], message='Key 模型清单' + label + '，请检查配置后重试')
        models.extend(result.get('models', []))
    return dict(state='ready', models=list(dict.fromkeys(models)), source='key',
                message='此 Key 的模型接口返回清单')


class KeyModelCatalog:
    """Two lazy workers; a slow supplier never holds a DB transaction or UI request."""
    def __init__(self, fetcher=fetch, clock=time.time):
        self.fetcher, self.clock = fetcher, clock
        self.lock = threading.Lock()
        self.entries, self.pending = {}, set()
        self.queue = queue.Queue(maxsize=64)
        self.stop = threading.Event()
        self.threads = []

    def snapshot(self, row, cfg, rule):
        if not row['enabled']:
            return dict(state='disabled', models=[], message='此 Key 已停用')
        if cfg.get('models'):
            return dict(state='ready', models=list(cfg['models']), source='binding',
                        message='此 Key 的后台绑定模型；绑定优先于自动拉取')
        if not rule:
            return dict(state='unsupported', models=[], message='适配包未提供模型读取接口，请更新适配包或绑定模型')
        key = (row['id'], row['revision'], rule_digest(rule))
        now = self.clock()
        with self.lock:
            if not self.threads and not self.stop.is_set():
                self.threads = [threading.Thread(target=self.work, daemon=True, name=f'key-model-catalog-{i}') for i in range(2)]
                for thread in self.threads:
                    thread.start()
            entry = self.entries.get(key)
            if key not in self.pending and not self.stop.is_set() and (entry is None or now >= entry['next_at']):
                try:
                    self.queue.put_nowait((key, copy.deepcopy(rule), copy.deepcopy(cfg)))
                    self.pending.add(key)
                except queue.Full:
                    pass
            value = copy.deepcopy(entry['value'] if entry else dict(state='loading', models=[], message='正在读取此 Key 的模型清单'))
            value['refreshing'] = key in self.pending
            if value.get('fetched_at') and now - value['fetched_at'] >= 300:
                value.update(state='loading', models=[], message='正在重新确认此 Key 的模型清单')
            return value

    def work(self):
        while not self.stop.is_set():
            try:
                key, rule, cfg = self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                try:
                    value = self.fetcher(rule, cfg)
                except Exception:
                    value = dict(state='error', models=[], message='Key 模型清单读取失败，请检查配置后重试')
                now = self.clock()
                value['fetched_at'] = int(now)
                with self.lock:
                    # Old revisions can finish but can never be read by a newer revision.
                    self.entries[key] = dict(value=value, next_at=now + (300 if value['state'] == 'ready' else 30))
                    while len(self.entries) > 128:
                        self.entries.pop(next(iter(self.entries)))
            finally:
                with self.lock:
                    self.pending.discard(key)
                self.queue.task_done()

    def close(self):
        self.stop.set()
