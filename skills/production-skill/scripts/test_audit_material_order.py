"""Reference validity and executor row-order compatibility are separate checks."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from audit_material_refs import audit


def image(key, refs=()):
    return dict(kind='image', key=key, filename=key + '.png',
                reference_images=[dict(image_n=i, key=p) for i, p in enumerate(refs, 1)],
                prompt='\n'.join(f'Image {i} = {p}，这张图是母图。' for i, p in enumerate(refs, 1)) + '\n完整画面。')


def fixture():
    return [dict(kind='manifest', total=2, image=2, video=0),
            image('child', ['parent']), image('parent')]


class MaterialOrderTests(unittest.TestCase):
    def test_forward_reference_valid_without_mutation(self):
        rows = fixture()
        before = copy.deepcopy(rows)
        r = audit(rows)
        self.assertEqual(r['errors'], [])
        self.assertEqual(r['forward_references'], [dict(task='child', parent='parent')])
        self.assertEqual(r['layers']['material'], 'basic_fields_and_references_passed')
        self.assertEqual(r['layers']['execution_order'], 'not_checked')
        self.assertEqual(rows, before)

    def test_explicit_sequential_executor_fails_only_order_layer(self):
        r = audit(fixture(), require_parent_first=True)
        self.assertEqual(r['layers']['material'], 'basic_fields_and_references_passed')
        self.assertEqual(r['layers']['execution_order'], 'failed')
        self.assertEqual(len(r['errors']), 1)
        self.assertIn('Sequential executor', r['errors'][0])

    def test_sorted_rows_pass_sequential_check_not_readiness(self):
        rows = fixture()
        rows[1:] = reversed(rows[1:])
        r = audit(rows, require_parent_first=True)
        self.assertEqual(r['errors'], [])
        self.assertEqual(r['layers']['execution_order'], 'parent_first_row_order_passed')
        self.assertEqual(r['layers']['files'], 'not_checked')
        self.assertEqual(r['layers']['submission'], 'not_checked')

    def test_missing_parent_still_fails(self):
        rows = fixture()
        rows[1] = image('child', ['absent'])
        for strict in (False, True):
            r = audit(rows, require_parent_first=strict)
            self.assertTrue(any('missing image task' in e for e in r['errors']))
            self.assertEqual(r['layers']['material'], 'failed')
            self.assertEqual(r['layers']['execution_order'], 'not_checked')

    def test_cycle_still_fails(self):
        rows = fixture()
        rows[2] = image('parent', ['child'])
        r = audit(rows)
        self.assertTrue(any('cyclic reference' in e for e in r['errors']))

    def test_self_reference_still_fails(self):
        rows = fixture()
        rows[1] = image('child', ['child'])
        r = audit(rows)
        self.assertTrue(any('cyclic reference' in e for e in r['errors']))

    def test_missing_actual_files_still_fails(self):
        r = audit(fixture(), files={})
        self.assertEqual(r['layers']['material'], 'basic_fields_and_references_passed')
        self.assertEqual(r['layers']['files'], 'failed')
        self.assertTrue(any('missing actual file' in e for e in r['errors']))

    def test_prompt_and_reference_number_checks_unchanged(self):
        rows = fixture()
        rows[1]['reference_images'][0]['image_n'] = 2
        r = audit(rows)
        self.assertTrue(any('non-contiguous Image numbers' in e for e in r['errors']))
        self.assertTrue(any('prompt bindings differ' in e for e in r['errors']))

    def test_video_before_image_valid(self):
        rows = fixture()
        rows[0].update(image=1, video=1)
        rows[1] = dict(kind='video', key='EP01-SEG01', filename='v.mp4',
                       storyboard_refs=[dict(image_n=1, key='parent')],
                       prompt='Image 1 = parent，这张图是本段空间参考。')
        self.assertEqual(audit(rows)['errors'], [])
        self.assertEqual(audit(rows, require_parent_first=True)['layers']['execution_order'], 'failed')

    def test_cli_default_and_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'material.jsonl'
            p.write_text('\n'.join(json.dumps(r) for r in fixture()), encoding='utf-8')
            cmd = [sys.executable, str(Path(__file__).with_name('audit_material_refs.py')), str(p)]
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(cmd + ['--require-parent-first'], capture_output=True).returncode, 1)


if __name__ == '__main__':
    unittest.main()
