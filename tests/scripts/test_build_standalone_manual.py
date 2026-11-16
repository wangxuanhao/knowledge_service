"""Standalone manual rendering regressions, independent of application storage."""
import runpy
import unittest
from pathlib import Path


class ManualTableTests(unittest.TestCase):
    def test_escaped_pipe_stays_inside_table_cell_and_inline_command(self):
        builder = runpy.run_path(str(Path(__file__).resolve().parents[2] /
                                    'scripts' / 'build_standalone_manual.py'))
        rendered, _ = builder['render'](builder['tokenize']([
            '| 状态 | 命令 |', '| --- | --- |',
            r'| 查端口 | `netstat -ano \| findstr 8100` |']))
        self.assertEqual(rendered.count('<td>'), 2)
        self.assertIn('<code>netstat -ano | findstr 8100</code>', rendered)


if __name__ == '__main__':
    unittest.main()
