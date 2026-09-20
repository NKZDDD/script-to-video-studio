"""Contract routing and exact direct-reference regression tests."""
import copy
import unittest

from audit_material_refs import audit
from build_board_lite import parse_md
from test_audit_material_order import image
from test_audit_abc_flow import fixture, task, run


class ContractRoutingTests(unittest.TestCase):
    def setUp(self):
        self.video = dict(kind='video', key='opening', filename='opening.mp4',
                          prompt='独立空云短镜。', storyboard_refs=[], reference_images=[])

    def test_internal_reference_free_task_not_claimed_ready(self):
        result = audit([self.video], profile='internal')
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['layers']['files'], 'not_checked')
        self.assertEqual(result['layers']['submission'], 'not_checked')

    def test_default_v3_guards_preserved(self):
        result = audit([dict(kind='manifest', total=1, image=0, video=1), self.video])
        self.assertTrue(any('EPxx-SEGxx' in e for e in result['errors']))
        self.assertTrue(any('non-empty storyboard_refs' in e for e in result['errors']))

    def test_unknown_profile_fails_closed(self):
        self.assertTrue(audit([self.video], profile='typo')['errors'])

    def test_internal_manifest_if_supplied_still_checked(self):
        for rows in ([self.video, dict(kind='manifest', total=1, image=0, video=1)],
                     [dict(kind='manifest', total=2, image=0, video=1), self.video]):
            self.assertTrue(audit(rows, profile='internal')['errors'])

    def test_internal_missing_cycle_and_binding_checks_preserved(self):
        cases = [([image('child', ['absent'])], 'missing image task'),
                 ([image('a', ['b']), image('b', ['a'])], 'cyclic reference')]
        bad = image('a', ['b']); bad['prompt'] = '完整画面。'
        cases.append(([bad, image('b')], 'prompt bindings differ'))
        for rows, message in cases:
            with self.subTest(message=message):
                self.assertTrue(any(message in e for e in audit(rows, profile='internal')['errors']))

    def test_internal_upload_comparison_unchanged(self):
        rows = [image('parent'), image('child', ['parent'])]
        self.assertEqual(audit(rows, profile='internal')['errors'], [])
        result = audit(rows, profile='internal', uploads={'child': []})
        self.assertEqual(result['layers']['submission'], 'failed')


class BoardDirectReferenceTests(unittest.TestCase):
    def test_prefix_plain_mention_and_parent_board_not_direct_refs(self):
        raw = ('【参考图】\nImage 1 = CHR-10，这张图是人物。\n'
               'Image 2 = PRJ__BOARD_A_R01，这张图是结尾。\n'
               '【时间轴分镜】\n[镜头1]\n画面：只提到CHR-1与@CHR-2，不绑定。')
        keys = ['CHR-1', 'CHR-10', 'CHR-2', 'PRJ__BOARD', 'PRJ__BOARD_A_R01']
        md = '# 测试\n## P2 资产清单\n| 编码 | 名字 |\n|---|---|\n'
        md += '\n'.join(f'| {k} | 测试 |' for k in keys)
        md += '\n## P4 投喂提示词\n### 段1\n```text\n' + raw + '\n```\n'
        data = parse_md(md)
        found = {r['code']: r['refs'] for r in data['assets']}
        self.assertEqual(found, {k: [1] if k in ('CHR-10', 'PRJ__BOARD_A_R01') else [] for k in keys})
        self.assertEqual(data['segments'][0]['raw'], raw)


class CrossBoardSourceTests(unittest.TestCase):
    def test_two_boundaries_use_shared_master_and_reject_prior_c(self):
        rows, ledger = fixture()
        regions = {r: 'board2_' + r for r in 'ABC'}
        rows[3] = task('EP01-SEG02', 'video', ['board_R01_B', 'board_R01_C', regions['A']])
        rows += [task('board2', refs=['mother_A']), task('EP01-SEG03', 'video', [regions['B'], regions['C']])]
        ledger['video_order'].append('EP01-SEG03')
        ledger['images']['board2'] = dict(role='abc_board', method='generate_once', boundary='edge2')
        ledger['boundaries'].append(dict(id='edge2', from_seg='EP01-SEG02', to_seg='EP01-SEG03',
                                         use_abc=True, reason='同场连续', board_key='board2', region_keys=regions))
        for r,k in regions.items():
            rows.append(task(k, refs=['board2']))
            ledger['images'][k] = dict(role='abc_panel', method='generate_once', boundary='edge2', board_key='board2', region=r)
        self.assertEqual(run(rows, ledger)['errors'], [])
        changed = copy.deepcopy(rows)
        idx = next(i for i,r in enumerate(changed) if r['key']=='board2')
        changed[idx] = task('board2', refs=['board_R01_C'])
        self.assertIn('panel_dependency', {e['code'] for e in run(changed, ledger)['errors']})


if __name__ == '__main__':
    unittest.main()
