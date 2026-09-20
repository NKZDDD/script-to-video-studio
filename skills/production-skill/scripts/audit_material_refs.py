"""Partial basic-field/reference audit, NOT a complete schema/import validator.

--profile v3 (default): retain V3.0-specific basic guards.
--profile internal: normalized internal records for other delivery contracts.
Internal records are an audit adapter, not an external delivery schema.

--files: {image_key: absolute_local_image_path}
--uploads: {task_key: [{image_n: 1, key: image_key, source: actual_request_source}]}
--require-parent-first: check row order ONLY for a confirmed sequential executor.
Forward references are valid by default; missing parents and cycles are errors.
Upload records must be extracted from actual assembled requests, not copied from
planned materials. No network delivery or provider receipt is tested here.
"""
import argparse
import json
import re
from pathlib import Path


def _audit_refs(rows, files=None):
    errors, notes = [], []
    tasks = [r for r in rows if r.get('kind') != 'manifest']
    images = {r['key']: r for r in tasks if r.get('kind') == 'image'}
    keys = [r.get('key') for r in tasks]
    if len(set(keys)) != len(keys):
        errors.append('Duplicate task keys')
    graph, seen, needed = {}, set(), set()
    forward_references = []
    edges = 0
    for row in tasks:
        k = row['key']
        rr = row.get('storyboard_refs', []) + row.get('reference_images', [])
        pairs = [(r.get('image_n'), r.get('key', r.get('asset_id'))) for r in rr]
        graph[k] = [r for _, r in pairs]
        edges += len(pairs)
        if [n for n, _ in pairs] != list(range(1, len(pairs) + 1)):
            errors.append(f'{k}: non-contiguous Image numbers')
        if len(set(r for _, r in pairs)) != len(pairs):
            errors.append(f'{k}: duplicate uploaded image')
        for _, parent in pairs:
            needed.add(parent)
            if parent not in images:
                errors.append(f'{k}: missing image task {parent}')
            elif parent not in seen and parent != k:
                forward_references.append({'task': k, 'parent': parent})
        text = row.get('prompt', '')
        binds = [(int(n), r) for n, r in re.findall(r'Image\s+(\d+)\s*=\s*([^\s,，;；。]+)', text)]
        if set(binds) != set(pairs) or len(binds) != len(pairs):
            errors.append(f'{k}: prompt bindings differ from reference arrays')
        used = {int(n) for n in re.findall(r'Image\s+(\d+)', text)}
        if used - {n for n, _ in binds}:
            errors.append(f'{k}: unbound Image numbers')
        for parent in set(re.findall(r'PRJ_[A-Za-z0-9_]+', text)):
            if parent not in images:
                errors.append(f'{k}: missing image key in prompt: {parent}')
            elif parent != k and parent not in graph[k]:
                errors.append(f'{k}: prompt key not uploaded: {parent}')
        seen.add(k)
    active, done = set(), set()
    def visit(k):
        if k in active:
            errors.append(f'{k}: cyclic reference')
            return
        if k in done:
            return
        active.add(k)
        for parent in graph.get(k, []):
            if parent in graph:
                visit(parent)
        active.remove(k)
        done.add(k)
    for k in graph:
        visit(k)
    if files is None:
        notes.append('Actual image files NOT checked; task existence does not prove generation succeeded.')
    else:
        from PIL import Image
        for k in sorted(needed):
            path = Path(files[k]) if files.get(k) else None
            if path is None or not path.is_absolute() or not path.is_file():
                errors.append(f'{k}: missing actual file or absolute path')
                continue
            try:
                with Image.open(path) as im:
                    im.verify()
            except Exception as exc:
                errors.append(f'{k}: unreadable image: {exc}')
        notes.append('File checks do not verify visual content or task success logs.')
    return dict(image_tasks=len(images), reference_edges=edges, errors=errors, notes=notes,
                forward_references=forward_references)


def audit(rows, files=None, uploads=None, *, require_parent_first=False, profile='v3'):
    """Apply selected basic guards; retain graph/upload validation in both modes."""
    errors, notes = [], []
    layers = {'material': 'not_checked', 'files': 'not_checked', 'submission': 'not_checked',
              'execution_order': 'not_checked'}
    if profile not in ('v3', 'internal'):
        errors.append(f'Unknown audit profile: {profile!r}')
    elif not isinstance(rows, list) or not rows:
        errors.append('Empty material: no tasks to audit')
    elif any(not isinstance(row, dict) for row in rows):
        errors.append('Every material line must be a JSON object')
    else:
        manifests = [r for r in rows if r.get('kind') == 'manifest']
        if ((profile == 'v3' or manifests)
                and (rows[0].get('kind') != 'manifest' or len(manifests) != 1)):
            errors.append('Exactly one manifest is required at the first line')
        tasks = [r for r in rows if r.get('kind') != 'manifest']
        if not tasks:
            errors.append('Material contains no image/video tasks')
        for index, row in enumerate(tasks, 1):
            if row.get('kind') not in ('image', 'video'):
                errors.append(f'task {index}: unknown kind {row.get("kind")!r}')
            for field in ('key', 'filename', 'prompt'):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    errors.append(f'task {index}: non-empty string {field} required')
            filename = row.get('filename')
            if isinstance(filename, str) and any(c in filename for c in '/\\:'):
                errors.append(f'task {index}: filename must not contain a path')
            if row.get('kind') == 'video' and profile == 'v3':
                if not isinstance(row.get('key'), str) or not re.fullmatch(r'EP\d{2,}-SEG\d{2,}', row['key']):
                    errors.append(f'task {index}: invalid EPxx-SEGxx key')
                if not isinstance(row.get('storyboard_refs'), list) or not row['storyboard_refs']:
                    errors.append(f'task {index}: V3.0 requires non-empty storyboard_refs')
            for field in ('storyboard_refs', 'reference_images'):
                refs = row.get(field, [])
                if not isinstance(refs, list):
                    errors.append(f'task {index}: {field} must be an array')
                    continue
                for ref in refs:
                    if (not isinstance(ref, dict) or type(ref.get('image_n')) is not int
                            or ref['image_n'] < 1 or not isinstance(ref.get('key', ref.get('asset_id')), str)
                            or not ref.get('key', ref.get('asset_id'))):
                        errors.append(f'task {index}: invalid reference image_n/key')
        filenames = [r['filename'] for r in tasks if isinstance(r.get('filename'), str)]
        if len(set(filenames)) != len(filenames):
            errors.append('Duplicate task filenames')
        if manifests:
            counts = {'total': len(tasks), 'image': sum(r.get('kind') == 'image' for r in tasks),
                      'video': sum(r.get('kind') == 'video' for r in tasks)}
            for field, actual in counts.items():
                declared = manifests[0].get(field)
                if type(declared) is not int or declared != actual:
                    errors.append(f'manifest {field}: declared {declared!r}, actual {actual}')
    notes.append(f'Profile {profile}: partial audit only, not full external schema/import validation. Episode/segment totals, shape routing, necessary visual coverage, identity descriptions, timing and visual correctness require separate checks.')
    if errors:
        layers['material'] = 'failed'
        return dict(image_tasks=0, reference_edges=0, errors=errors, notes=notes, layers=layers, profile=profile)
    result = _audit_refs(rows)
    errors.extend(result['errors'])
    layers['material'] = 'failed' if errors else 'basic_fields_and_references_passed'
    if require_parent_first:
        if result['errors']:
            notes.append('Parent-first executor compatibility NOT checked because material references are invalid.')
        else:
            order_errors = [f"Sequential executor row-order mismatch: {ref['task']} precedes parent {ref['parent']}"
                            for ref in result['forward_references']]
            errors.extend(order_errors)
            layers['execution_order'] = 'failed' if order_errors else 'parent_first_row_order_passed'
            notes.append('Row order checked only for the explicitly selected parent-first executor; not a general V3.0 contract rule.')
    elif result['forward_references']:
        notes.append('Forward references recorded as ordering information, not material errors. Actual executor must resolve dependencies before generation.')
    notes.append('Row-order checks do not verify scheduling, task completion or parent-file readiness; actual execution must wait for valid parent images.')
    if files is None:
        notes.append('Actual parent files NOT checked; task existence does not prove generation succeeded.')
    elif not isinstance(files, dict) or any(not isinstance(v, str) for v in files.values()):
        errors.append('--files must map image keys to absolute path strings')
        layers['files'] = 'failed'
    else:
        file_result = _audit_refs(rows, files)
        file_errors = [error for error in file_result['errors'] if error not in result['errors']]
        errors.extend(file_errors)
        layers['files'] = 'failed' if file_errors else 'readability_passed'
        notes.append('File readability does not verify visual identity, revision identity or task success logs.')
    if uploads is None:
        notes.append('Actual request uploads NOT checked; writing Image N in a prompt does not upload an image.')
    elif not isinstance(uploads, dict):
        errors.append('--uploads must map task keys to ordered actual-request image records')
        layers['submission'] = 'failed'
    else:
        before = len(errors)
        by_key = {r['key']: r for r in tasks}
        for key, records in uploads.items():
            if key not in by_key or not isinstance(records, list):
                errors.append(f'{key}: unknown submission task or non-array records')
                continue
            expected = [(r['image_n'], r.get('key', r.get('asset_id')))
                        for r in by_key[key].get('storyboard_refs', []) + by_key[key].get('reference_images', [])]
            actual = []
            for record in records:
                if not isinstance(record, dict):
                    errors.append(f'{key}: upload record must be an object')
                    continue
                actual.append((record.get('image_n'), record.get('key', record.get('asset_id'))))
                if not isinstance(record.get('source'), str) or not record['source'].strip():
                    errors.append(f'{key}: uploaded reference has no actual request source')
            if actual != expected:
                errors.append(f'{key}: actual request images/order differ from material references')
        omitted = sorted(set(by_key) - set(uploads))
        layers['submission'] = ('failed' if len(errors) > before else
                                'partial_manifest_compared' if omitted else 'manifest_compared')
        notes.append('Submission comparison trusts the supplied request extraction; no network delivery, provider receipt or source-to-image identity verified.')
        if omitted:
            notes.append('Submission tasks NOT checked: ' + ', '.join(omitted))
    result.update(errors=errors, notes=notes, layers=layers, profile=profile)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('material', type=Path)
    parser.add_argument('--profile', choices=('v3', 'internal'), default='v3',
                        help='v3: V3.0 basic guards; internal: normalized records for other contracts')
    parser.add_argument('--files', type=Path, help='JSON mapping of image key to actual absolute file path')
    parser.add_argument('--uploads', type=Path, help='JSON ordered image records extracted from actual assembled requests')
    parser.add_argument('--require-parent-first', action='store_true',
                        help='Use only when the actual executor is confirmed to require parents earlier in file order')
    args = parser.parse_args()
    try:
        rows = [json.loads(s) for s in args.material.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
        files = json.loads(args.files.read_text(encoding='utf-8-sig')) if args.files else None
        uploads = json.loads(args.uploads.read_text(encoding='utf-8-sig')) if args.uploads else None
        result = audit(rows, files, uploads, require_parent_first=args.require_parent_first, profile=args.profile)
    except (OSError, ValueError) as exc:
        result = {'errors': [str(exc)], 'notes': ['Audit incomplete; no import/readiness claim is valid.']}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['errors']))
