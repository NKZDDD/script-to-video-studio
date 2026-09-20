"""Regression tests for ABC topology; fixtures do not assert visual correctness."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from audit_abc_flow import audit, material_digest


def task(key, kind='image', refs=(), prompt=''):
    row = dict(kind=kind, key=key, filename=key + ('.png' if kind == 'image' else '.mp4'))
    row['reference_images'] = [dict(image_n=i, key=k) for i, k in enumerate(refs, 1)]
    row['prompt'] = '\n'.join(f'Image {i} = {k}，这张图是本镜参考。' for i, k in enumerate(refs, 1)) + '\n' + prompt
    return row


def fixture():
    rows = [task('mother_A'), task('board_R01', refs=['mother_A']),
            task('EP01-SEG01', 'video', ['board_R01_A']), task('EP01-SEG02', 'video', ['board_R01_B', 'board_R01_C'])]
    ledger = dict(version=2, reviewed=True,
                  images={'mother_A': dict(role='asset', method='generate_once'),
                          'board_R01': dict(role='abc_board', method='generate_once', boundary='edge1')},
                  video_order=['EP01-SEG01', 'EP01-SEG02'],
                  boundaries=[dict(id='edge1', from_seg='EP01-SEG01', to_seg='EP01-SEG02',
                                   use_abc=True, reason='同场对白承接', board_key='board_R01')])
    regions = {r: 'board_R01_' + r for r in 'ABC'}
    ledger['boundaries'][0]['region_keys'] = regions
    for r, k in regions.items():
        rows.append(task(k, refs=['board_R01']))
        ledger['images'][k] = dict(role='abc_panel', method='generate_once', boundary='edge1', board_key='board_R01', region=r)
    return rows, ledger


def run(rows, ledger):
    ledger['material_sha256'] = material_digest(rows)
    return audit(rows, ledger)


class ABCFlowTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.ledger = fixture()

    def codes(self, result):
        return {x['code'] for x in result['errors']}

    def test_one_board_same_version_and_ordinary_asset_suffix(self):
        self.assertEqual(run(self.rows, self.ledger)['status'], 'declared_flow_checked')

    def test_input_not_mutated(self):
        self.ledger['material_sha256'] = material_digest(self.rows)
        before = copy.deepcopy((self.rows, self.ledger))
        audit(self.rows, self.ledger)
        self.assertEqual((self.rows, self.ledger), before)

    def test_row_order_not_used_as_dependency_rule(self):
        self.assertEqual(run(list(reversed(self.rows)), self.ledger)['status'], 'declared_flow_checked')

    def test_split_with_arbitrary_names_and_assembly_rejected(self):
        for key, role in [('alpha', 'abc_panel'), ('beta', 'abc_panel'), ('merge', 'abc_composite')]:
            self.rows.append(task(key))
            self.ledger['images'][key] = dict(role=role, method='generate_once', boundary='edge1')
        self.rows[1] = task('board_R01', refs=['merge'])
        codes = self.codes(run(self.rows, self.ledger))
        self.assertTrue({'composite_task', 'panel_dependency'} <= codes)

    def test_indirect_panel_dependency(self):
        self.rows += [task('panel'), task('wrapper', refs=['panel'])]
        self.rows[1] = task('board_R01', refs=['wrapper'])
        self.ledger['images'].update(panel=dict(role='abc_panel', method='generate_once', boundary='edge1'),
                                     wrapper=dict(role='asset', method='generate_once'))
        self.assertIn('panel_dependency', self.codes(run(self.rows, self.ledger)))

    def test_different_versions(self):
        self.rows.append(task('board_R02', refs=['mother_A']))
        self.rows[3] = task('EP01-SEG02', 'video', ['board_R02'])
        self.ledger['images']['board_R02'] = dict(role='abc_board', method='generate_once', boundary='edge1')
        self.assertIn('board_count_or_version', self.codes(run(self.rows, self.ledger)))

    def test_one_endpoint_omits_board(self):
        self.rows[3] = task('EP01-SEG02', 'video', ['mother_A'])
        self.assertIn('endpoint_region_mismatch', self.codes(run(self.rows, self.ledger)))

    def test_missing_image_task(self):
        self.rows.pop(1)
        self.assertIn('missing_image_task', self.codes(run(self.rows, self.ledger)))

    def test_reference_without_task_or_catalog(self):
        self.rows[3] = task('EP01-SEG02', 'video', ['missing_R01'])
        self.assertIn('missing_reference', self.codes(run(self.rows, self.ledger)))

    def test_prompt_and_structured_refs_disagree(self):
        self.rows[3]['prompt'] = 'Image 1 = board_R02，这张图是入板。'
        self.assertIn('prompt_reference_mismatch', self.codes(run(self.rows, self.ledger)))

    def test_natural_transition_needs_no_board(self):
        self.rows = [self.rows[0], task('EP01-SEG01', 'video', ['mother_A']), task('EP01-SEG02', 'video', ['mother_A'])]
        self.ledger['images'] = {'mother_A': dict(role='asset', method='generate_once')}
        b = self.ledger['boundaries'][0]
        b.update(use_abc=False, reason='无关联天然转场')
        del b['board_key']
        del b['region_keys']
        self.assertEqual(run(self.rows, self.ledger)['status'], 'declared_flow_checked')

    def test_natural_transition_cannot_hide_scheduled_board(self):
        self.ledger['boundaries'][0]['use_abc'] = False
        self.assertIn('natural_transition_has_abc', self.codes(run(self.rows, self.ledger)))

    def test_missing_decision_incomplete(self):
        self.ledger['boundaries'] = []
        r = run(self.rows, self.ledger)
        self.assertTrue(any(x['code'] == 'missing_boundary_decision' for x in r['review_required']))

    def test_missing_ledger_does_not_pass(self):
        self.assertEqual(audit(self.rows)['status'], 'incomplete')

    def test_stale_or_unreviewed_ledger(self):
        self.ledger['material_sha256'] = 'old'
        self.ledger['reviewed'] = False
        r = audit(self.rows, self.ledger)
        self.assertEqual(r['status'], 'incomplete')
        self.assertEqual({x['code'] for x in r['review_required']}, {'stale_ledger', 'unreviewed_ledger'})

    def test_cycles_fail(self):
        self.rows[0] = task('mother_A', refs=['board_R01'])
        self.assertIn('cyclic_dependency', self.codes(run(self.rows, self.ledger)))

    def test_unclassified_image_incomplete(self):
        self.rows.append(task('unknown'))
        self.assertEqual(run(self.rows, self.ledger)['status'], 'incomplete')

    def test_obvious_panel_disguised_as_asset_requires_review(self):
        self.rows[0]['prompt'] = '仅制作单幅，作为A区源图。'
        self.assertEqual(run(self.rows, self.ledger)['status'], 'incomplete')

    def test_reuse_requires_file_and_matching_hash(self):
        self.rows.pop(1)
        m = self.ledger['images']['board_R01']
        m.update(method='reuse', reuse_reviewed=True)
        self.assertIn('missing_existing_resource', self.codes(run(self.rows, self.ledger)))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'existing.png'
            # Only byte identity/readability is tested, explicitly not image validity.
            p.write_bytes(b'fixture')
            m.update(path=str(p), sha256=hashlib.sha256(b'fixture').hexdigest())
            self.assertEqual(run(self.rows, self.ledger)['status'], 'declared_flow_checked')
            p.write_bytes(b'changed')
            self.assertIn('missing_existing_resource', self.codes(run(self.rows, self.ledger)))

    def test_multiple_scenes_do_not_lose_references(self):
        for k in ['scene2', 'scene3']:
            self.rows.append(task(k))
            self.ledger['images'][k] = dict(role='asset', method='generate_once')
        self.rows[3] = task('EP01-SEG02', 'video', ['board_R01_B', 'board_R01_C', 'scene2', 'scene3'])
        self.assertEqual(run(self.rows, self.ledger)['status'], 'declared_flow_checked')

    def test_three_segments_incoming_outgoing_and_wrong_boundary(self):
        self.rows += [task('next_R01', refs=['mother_A']), task('EP01-SEG03', 'video', ['next_R01_B', 'next_R01_C'])]
        self.rows[3] = task('EP01-SEG02', 'video', ['board_R01_B', 'board_R01_C', 'next_R01_A'])
        self.ledger['images']['next_R01'] = dict(role='abc_board', method='generate_once', boundary='edge2')
        self.ledger['video_order'].append('EP01-SEG03')
        self.ledger['boundaries'].append(dict(id='edge2', from_seg='EP01-SEG02', to_seg='EP01-SEG03',
                                             use_abc=True, reason='连续走位', board_key='next_R01'))
        regions = {r: 'next_R01_' + r for r in 'ABC'}
        self.ledger['boundaries'][-1]['region_keys'] = regions
        for r, k in regions.items():
            self.rows.append(task(k, refs=['next_R01']))
            self.ledger['images'][k] = dict(role='abc_panel', method='generate_once', boundary='edge2', board_key='next_R01', region=r)
        self.assertEqual(run(self.rows, self.ledger)['status'], 'declared_flow_checked')
        self.rows[2] = task('EP01-SEG01', 'video', ['board_R01_B', 'board_R01_C', 'next_R01_A'])
        self.assertIn('wrong_boundary_reference', self.codes(run(self.rows, self.ledger)))

    def test_old_ledger_rejected(self):
        self.ledger['version'] = 1
        self.assertIn('invalid_ledger', self.codes(run(self.rows, self.ledger)))

    def test_full_board_cannot_control_video(self):
        self.rows[3] = task('EP01-SEG02', 'video', ['board_R01', 'board_R01_B', 'board_R01_C'])
        self.assertIn('whole_board_in_video', self.codes(run(self.rows, self.ledger)))

    def test_incoming_c_missing(self):
        self.rows[3] = task('EP01-SEG02', 'video', ['board_R01_B'])
        self.assertIn('endpoint_region_mismatch', self.codes(run(self.rows, self.ledger)))

    def test_endpoints_swapped(self):
        self.rows[2] = task('EP01-SEG01', 'video', ['board_R01_B'])
        self.rows[3] = task('EP01-SEG02', 'video', ['board_R01_A', 'board_R01_C'])
        self.assertIn('endpoint_region_mismatch', self.codes(run(self.rows, self.ledger)))

    def test_panel_extra_reference_rejected(self):
        self.rows[4] = task('board_R01_A', refs=['board_R01', 'mother_A'])
        self.assertIn('panel_reference_mismatch', self.codes(run(self.rows, self.ledger)))

    def test_parent_version_mismatch(self):
        self.ledger['images']['board_R01_B']['board_key'] = 'board_R02'
        codes = self.codes(run(self.rows, self.ledger))
        self.assertTrue({'invalid_panel_parent', 'region_version_mismatch', 'panel_reference_mismatch'} <= codes)

    def test_region_label_mismatch(self):
        self.ledger['images']['board_R01_A']['region'] = 'B'
        self.assertIn('region_version_mismatch', self.codes(run(self.rows, self.ledger)))

    def test_missing_region_declaration(self):
        del self.ledger['boundaries'][0]['region_keys']['C']
        self.assertIn('invalid_region_set', self.codes(run(self.rows, self.ledger)))

    def test_duplicate_image_binding(self):
        self.rows[3] = task('EP01-SEG02', 'video', ['board_R01_B', 'board_R01_C', 'board_R01_C'])
        self.assertIn('duplicate_reference', self.codes(run(self.rows, self.ledger)))

    def test_reused_panel_lineage(self):
        self.rows.pop(4)
        m = self.ledger['images']['board_R01_A']
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'A.png'
            p.write_bytes(b'fixture')
            m.update(method='reuse', path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest(), reuse_reviewed=True)
            self.assertEqual(run(self.rows, self.ledger)['status'], 'incomplete')
            m['lineage_reviewed'] = True
            self.assertEqual(run(self.rows, self.ledger)['status'], 'declared_flow_checked')

    def test_malformed_panel_metadata(self):
        for field, value in [('region', []), ('board_key', {}), ('boundary', [])]:
            with self.subTest(field=field):
                rows, ledger = fixture()
                ledger['images']['board_R01_A'][field] = value
                self.assertEqual(run(rows, ledger)['status'], 'failed')

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            p, l = Path(d) / 'm.jsonl', Path(d) / 'l.json'
            p.write_text('\n'.join(json.dumps(r) for r in self.rows), encoding='utf-8')
            command = [sys.executable, str(Path(__file__).with_name('audit_abc_flow.py')), str(p)]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.ledger['material_sha256'] = material_digest(self.rows)
            l.write_text(json.dumps(self.ledger), encoding='utf-8')
            self.assertEqual(subprocess.run(command + ['--ledger', str(l)], capture_output=True).returncode, 0)
            self.ledger['images']['board_R01']['role'] = 'abc_composite'
            l.write_text(json.dumps(self.ledger), encoding='utf-8')
            self.assertEqual(subprocess.run(command + ['--ledger', str(l)], capture_output=True).returncode, 1)

    def test_malformed_inputs_report_without_crashing(self):
        for field, value in [('role', []), ('boundary', []), ('method', None)]:
            with self.subTest(field=field):
                rows, ledger = fixture()
                ledger['images']['board_R01'][field] = value
                self.assertEqual(run(rows, ledger)['status'], 'failed')
        self.assertEqual(audit([])['status'], 'failed')
        self.rows[3]['reference_images'][0]['image_n'] = True
        self.assertIn('reference_numbering', self.codes(run(self.rows, self.ledger)))


if __name__ == '__main__':
    unittest.main()
