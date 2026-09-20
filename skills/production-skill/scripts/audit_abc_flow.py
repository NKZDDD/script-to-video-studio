"""Audit v7.0 ABC task topology against a reviewed INTERNAL ledger.

This is not a V3 schema validator, semantic prompt judge, or visual/video test.
The ledger is never embedded in production prompts or delivered JSONL.
Exit: 0 declared flow checked, 1 errors, 2 incomplete/review required.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def material_digest(rows):
    data = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def audit(rows, ledger=None):
    errors, pending = [], []
    def issue(code, target, message):
        errors.append(dict(code=code, target=target, message=message))
    def review(code, target, message):
        pending.append(dict(code=code, target=target, message=message))
    def finish():
        return dict(status='failed' if errors else 'incomplete' if pending else 'declared_flow_checked',
                    errors=errors, review_required=pending,
                    material_sha256=material_digest(rows),
                    scope='Task/reference topology against reviewed classifications; no row-order requirement.',
                    not_checked=['Full import contract', 'Truth of ledger classifications and transition reasons',
                                 'Semantic A/B/C use in prose', 'Actual generation call count/upload',
                                 'Region-to-board visual fidelity and spatial/image identity',
                                 'Actual video endpoints/direct concatenation'])
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
        issue('invalid_material', 'material', 'Expected nonempty list of task objects')
        return finish()
    tasks = {}
    for r in rows:
        if r.get('kind') == 'manifest':
            continue
        k = r.get('key')
        if r.get('kind') not in ('image', 'video') or not isinstance(k, str) or not k:
            issue('invalid_task', str(k), 'Expected image/video with nonempty key')
        elif k in tasks:
            issue('duplicate_key', k, 'Task key repeated')
        else:
            tasks[k] = r
    images = {k: r for k, r in tasks.items() if r['kind'] == 'image'}
    videos = {k: r for k, r in tasks.items() if r['kind'] == 'video'}
    if not videos:
        review('no_videos', 'material', 'No video boundary scope to audit')
    if ledger is None:
        review('ledger_required', 'ledger', 'Review task purposes and all adjacent boundaries; supply internal ledger')
        for k, r in images.items():
            if re.search(r'ABC|交接[A-C]取景|作为[A-C]区源图', str(r.get('name', '')) + str(r.get('prompt', ''))):
                review('abc_candidate', k, 'Possible board/panel task; name alone cannot classify its function')
        return finish()
    if not isinstance(ledger, dict) or type(ledger.get('version')) is not int or ledger.get('version') != 2:
        issue('invalid_ledger', 'ledger', 'Expected internal ledger version 2 (board -> region derivatives -> video)')
        return finish()
    if ledger.get('material_sha256') != material_digest(rows):
        review('stale_ledger', 'ledger', 'Material changed or digest missing; review and rebuild ledger')
    if ledger.get('reviewed') is not True:
        review('unreviewed_ledger', 'ledger', 'Task semantics and boundary decisions have not been reviewed')
    meta = ledger.get('images')
    order = ledger.get('video_order')
    boundaries = ledger.get('boundaries')
    if (not isinstance(meta, dict) or not isinstance(order, list)
            or any(not isinstance(k, str) for k in order) or not isinstance(boundaries, list)):
        issue('invalid_ledger', 'ledger', 'images object, video_order string array and boundaries array required')
        return finish()
    if len(order) != len(set(order)) or set(order) != set(videos):
        issue('video_scope', 'video_order', 'Order must include every material video exactly once')
    for k in images.keys() - meta.keys():
        review('unclassified_image', k, 'Every image task needs a reviewed role')
    known_images = set(images)
    roles, reusable = {}, set()
    for k, m in meta.items():
        if not isinstance(m, dict) or m.get('role') not in ('asset', 'abc_board', 'abc_panel', 'abc_composite'):
            issue('invalid_role', k, 'role must be asset/abc_board/abc_panel/abc_composite')
            continue
        roles[k] = m['role']
        if k in tasks and k not in images:
            issue('invalid_image_catalog', k, 'Image record collides with a video task')
        method = m.get('method')
        if method not in ('generate_once', 'reuse'):
            issue('invalid_method', k, 'method must be generate_once or reuse')
        if method == 'reuse':
            path, digest = m.get('path'), m.get('sha256')
            try:
                p = Path(path) if isinstance(path, str) else None
                if p is None or not p.is_absolute() or not p.is_file() or p.stat().st_size == 0:
                    raise ValueError('Missing nonempty existing file at absolute path')
                if not isinstance(digest, str) or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                    raise ValueError('Existing file hash missing or mismatched')
                if m.get('reuse_reviewed') is not True:
                    review('reuse_unreviewed', k, 'Existing board/resource identity and reuse suitability need review')
                reusable.add(k)
                known_images.add(k)
            except (OSError, ValueError) as exc:
                issue('missing_existing_resource', k, str(exc))
        elif k not in images:
            issue('missing_image_task', k, 'Declared generated image has no image task')
        if m['role'] == 'abc_composite':
            issue('composite_task', k, 'Current flow uses a jointly generated board, not a second assembly resource')
        if m['role'] == 'abc_panel':
            if m.get('region') not in ('A', 'B', 'C') or not isinstance(m.get('board_key'), str) or not m['board_key']:
                issue('invalid_panel_lineage', k, 'Region derivative requires region A/B/C and exact board_key')
            if method == 'reuse' and m.get('lineage_reviewed') is not True:
                review('reuse_lineage_unreviewed', k, 'Review exact source board version and region of reused derivative')
        if m['role'] != 'asset' and not isinstance(m.get('boundary'), str):
            issue('missing_boundary', k, 'ABC role requires boundary id')
        if k in images and m['role'] == 'asset':
            # A review cue, never classify by an _A/_B suffix or arbitrary asset key.
            text = str(images[k].get('name', '')) + '\n' + str(images[k].get('prompt', ''))
            if re.search(r'作为[A-C]区源图|交接[A-C]取景|ABC交接板', text) and not m.get('classification_note'):
                review('role_conflict', k, 'ABC-related text labelled asset: review purpose and record classification_note')
    graph = {}
    for k, row in tasks.items():
        pairs = []
        for field in ('storyboard_refs', 'reference_images'):
            refs = row.get(field, [])
            if not isinstance(refs, list):
                issue('invalid_refs', k, field + ' must be an array')
                continue
            for ref in refs:
                if not isinstance(ref, dict) or not isinstance(ref.get('key', ref.get('asset_id')), str):
                    issue('invalid_refs', k, 'Reference key missing')
                    continue
                pairs.append((ref.get('image_n'), ref.get('key', ref.get('asset_id'))))
        valid_numbers = all(type(n) is int and n > 0 for n, _ in pairs)
        if not valid_numbers or [n for n, _ in pairs] != list(range(1, len(pairs) + 1)):
            issue('reference_numbering', k, 'Image numbers must follow actual ordered references')
        if len({p for _, p in pairs}) != len(pairs):
            issue('duplicate_reference', k, 'Same image bound more than once')
        prompt = row.get('prompt', '')
        binds = re.findall(r'Image\s+(\d+)\s*=\s*([^\s,，;；。]+)', prompt) if isinstance(prompt, str) else []
        if not valid_numbers or sorted((int(n), p) for n, p in binds) != sorted(pairs):
            issue('prompt_reference_mismatch', k, 'Prompt Image bindings differ from reference arrays')
        graph[k] = [p for _, p in pairs]
        for parent in graph[k]:
            if parent not in known_images:
                issue('missing_reference', k, 'Missing image task or verified reuse record: ' + parent)
    for k, m in meta.items():
        if roles.get(k) != 'abc_panel':
            continue
        parent = m.get('board_key')
        parent_meta = meta.get(parent) if isinstance(parent, str) else None
        if not isinstance(parent_meta, dict) or parent_meta.get('role') != 'abc_board' or parent_meta.get('boundary') != m.get('boundary'):
            issue('invalid_panel_parent', k, 'Derivative must name its boundary whole board with exact version')
        # Reused files still retain lineage; a material task, if supplied, must agree.
        if (k not in reusable or k in images) and graph.get(k) != [parent]:
            issue('panel_reference_mismatch', k, 'Only image input must be the declared whole board: ' + str(parent))
    # Board ancestors cannot be derivative panels; downstream panels are expected.
    # All tasks retain cycle checking, independent of material row order.
    for root in tasks:
        stack = [(root, frozenset())]
        visited = set()
        while stack:
            key, trail = stack.pop()
            if key in trail:
                issue('cyclic_dependency', root, 'Cycle includes ' + key)
                continue
            if key in visited:
                continue
            visited.add(key)
            if root != key and roles.get(root) == 'abc_board' and roles.get(key) in ('abc_panel', 'abc_composite'):
                if root not in reusable:
                    issue('panel_dependency', root, 'Whole board must precede, not depend on, region/assembly: ' + key)
            if key in reusable:  # Historical provenance is not a new generation dependency.
                continue
            stack.extend((p, trail | {key}) for p in graph.get(key, []))
    expected = set(zip(order, order[1:]))
    found, boundary_ids = set(), set()
    for b in boundaries:
        if not isinstance(b, dict):
            issue('invalid_boundary', 'ledger', 'Boundary must be an object')
            continue
        bid, a, z = b.get('id'), b.get('from_seg'), b.get('to_seg')
        if not all(isinstance(x, str) and x for x in (bid, a, z)):
            issue('invalid_boundary', 'ledger', 'Boundary id/from_seg/to_seg required')
            continue
        if bid in boundary_ids or (a, z) in found:
            issue('duplicate_boundary', bid, 'Boundary repeated')
        boundary_ids.add(bid)
        found.add((a, z))
        if (a, z) not in expected:
            issue('nonadjacent_boundary', bid, 'Boundary not adjacent in reviewed video order')
        if type(b.get('use_abc')) is not bool or not isinstance(b.get('reason'), str) or not b['reason'].strip():
            review('boundary_decision', bid, 'Explicit use_abc boolean and reviewed reason required')
            continue
        members = [k for k, m in meta.items() if isinstance(m, dict) and m.get('boundary') == bid and roles.get(k) != 'asset']
        boards = [k for k in members if roles.get(k) == 'abc_board']
        if not b['use_abc']:
            if members or b.get('board_key') or b.get('region_keys'):
                issue('natural_transition_has_abc', bid, 'Disabled boundary must not schedule/reference an ABC board')
            continue
        board = b.get('board_key')
        if not isinstance(board, str) or boards != [board]:
            issue('board_count_or_version', bid, 'Need exactly one selected whole board, with one exact version key')
            continue
        regions = b.get('region_keys')
        if (not isinstance(regions, dict) or set(regions) != {'A', 'B', 'C'}
                or any(not isinstance(k, str) or not k for k in regions.values())
                or len(set(regions.values())) != 3):
            issue('invalid_region_set', bid, 'Exactly one distinct derivative key for each A/B/C is required')
            continue
        panels = {k for k in members if roles.get(k) == 'abc_panel'}
        if panels != set(regions.values()):
            issue('panel_count', bid, 'Boundary must contain exactly its three declared derivatives')
        for region, key in regions.items():
            m = meta.get(key)
            if (not isinstance(m, dict) or m.get('role') != 'abc_panel'
                    or m.get('board_key') != board or m.get('boundary') != bid or m.get('region') != region):
                issue('region_version_mismatch', key, 'Derivative region/boundary/exact board version differs from selected boundary')
        for v, needed in ((a, {regions['A']}), (z, {regions['B'], regions['C']})):
            actual = {k for k in graph.get(v, []) if k in members}
            if actual != needed:
                issue('endpoint_region_mismatch', v, bid + ': expected only ' + ', '.join(sorted(needed)))
    for a, z in sorted(expected - found):
        review('missing_boundary_decision', a + ' -> ' + z, 'Classify related handoff or natural transition; do not assume no ABC')
    for k, m in meta.items():
        if (roles.get(k) not in (None, 'asset')
                and (not isinstance(m.get('boundary'), str) or m.get('boundary') not in boundary_ids)):
            issue('orphan_abc', k, 'ABC resource has no boundary in this scope')
    for v in videos:
        for k in graph.get(v, []):
            if roles.get(k) == 'abc_board':
                issue('whole_board_in_video', v, 'Upload reviewed independent regions, not the whole board: ' + k)
            if roles.get(k) in ('abc_panel', 'abc_board', 'abc_composite'):
                if not any(isinstance(b, dict) and b.get('use_abc') is True
                           and b.get('id') == meta[k].get('boundary')
                           and v in (b.get('from_seg'), b.get('to_seg')) for b in boundaries):
                    issue('wrong_boundary_reference', v, 'ABC belongs to another/disabled boundary: ' + k)
    return finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('material', type=Path)
    parser.add_argument('--ledger', type=Path, help='Reviewed internal ABC ledger (never a production-contract field)')
    args = parser.parse_args()
    try:
        rows = [json.loads(s) for s in args.material.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
        ledger = json.loads(args.ledger.read_text(encoding='utf-8-sig')) if args.ledger else None
        result = audit(rows, ledger)
    except (OSError, ValueError) as exc:
        result = dict(status='failed', errors=[dict(code='input_error', message=str(exc))])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['status'] == 'failed' else 2 if result['status'] == 'incomplete' else 0


if __name__ == '__main__':
    raise SystemExit(main())
