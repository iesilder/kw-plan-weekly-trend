"""The actual collection workflows must stage the canonical interest checkpoints."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CollectionPersistenceTests(unittest.TestCase):
    def test_every_collection_workflow_saves_new_and_updated_interest_checkpoint_files(self):
        for workflow in ('pipeline.yml', 'daily.yml', 'interests.yml'):
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ('config/interests.json', 'data/raw.sqlite3', 'data/daily-run.json',
                             'data/interests-collection.json', 'data/interests-registry.json',
                             'data/interests-requests.json', 'state/issues.json', 'site/data/interests.json'):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('{}\n')
                command = next(line.strip() for line in (ROOT / '.github/workflows' / workflow).read_text().splitlines()
                               if line.strip().startswith('git add '))
                subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
                subprocess.run(['bash', '-c', command], cwd=root, check=True, capture_output=True)
                checkpoint = 'data/interests-collection.json'
                staged = subprocess.check_output(['git', 'show', ':' + checkpoint], cwd=root, text=True)
                self.assertEqual(json.loads(staged), {})
                (root / checkpoint).write_text('{"schema_version":"1.0","entries":{},"last_attempt":null}\n')
                subprocess.run(['bash', '-c', command], cwd=root, check=True, capture_output=True)
                staged = subprocess.check_output(['git', 'show', ':' + checkpoint], cwd=root, text=True)
                self.assertEqual(json.loads(staged)['schema_version'], '1.0')
