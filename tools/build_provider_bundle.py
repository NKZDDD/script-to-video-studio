"""Export all maintained provider implementations, without local configuration."""
import argparse
import ast
import importlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from auth_service.studio_auth.adapter_format import digest, encode, validate


def build(version):
    from core.providers import _BUILTIN_ORDER, _classes_in
    modules, providers, monitors = {}, [], {}
    for name in _BUILTIN_ORDER:
        modules[name] = (ROOT / 'core/providers' / (name+'.py')).read_text(encoding='utf-8-sig')
        for cls in _classes_in(importlib.import_module('core.providers.'+name)):
            providers.append({'id': cls.id, 'name': cls.name, 'module': name, 'class': cls.__name__})
            monitors[cls.id] = dict(cls.monitor)
            if 'reason' not in monitors[cls.id]:
                monitors[cls.id]['base_url'] = cls.default_base_url
    # Keep protocol 1 unchanged: old clients accept an extra inert module. The
    # server reads this literal without importing/executing adapter code.
    modules['provider_monitor_catalog'] = 'MONITORS = ' + repr(monitors) + '\n'
    return validate(dict(protocol=1, version=version, modules=modules, providers=providers))


def runtime_imports(bundle):
    """Keep dependencies of downloaded code in the generic frozen runtime."""
    names = {'core.providers.base'}
    for source in bundle['modules'].values():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(x.name for x in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 1 or node.module == '__future__':
                    continue
                name = ('core.' if node.level == 2 else '')+(node.module or '')
                names.add(name.rstrip('.'))
                for item in node.names:
                    candidate = name.rstrip('.')+'.'+item.name
                    try:
                        if importlib.util.find_spec(candidate):
                            names.add(candidate)
                    except (ImportError, ValueError, AttributeError):
                        pass
    return sorted(n for n in names if n and (not n.startswith('core.providers.') or n == 'core.providers.base'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    bundle = build(args.version)
    with args.destination.open('xb') as stream:
        stream.write(encode(bundle))
    print({'providers': len(bundle['providers']), 'bytes': args.destination.stat().st_size, 'sha256': digest(bundle)})
