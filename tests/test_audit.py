import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from resume_claim_audit.cli import audit, claims, evidence, main, RESUME, NORESUME

RANGE = 'headers = {"Range": "bytes=100-"}'
CHECKPOINT = 'import json\nwith open("checkpoint.json") as f: state = json.load(f)\nwith open("checkpoint.json", "w") as f: json.dump(state, f)'
OFFSET = 'import os\nf = open("data", "ab")\nf.seek(os.path.getsize("data"))'


class AuditTests(unittest.TestCase):
    def fixture(self, doc, code=None, docpath='README.md', **options):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / docpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(doc)
            if code is not None:
                (root / 'app.py').write_text(code)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exitcode = main([directory, '--json'] + options.get('flags', []))
            return exitcode, json.loads(output.getvalue())

    def test_listed_fixtures(self):
        cases = [
            ('good/resume-range', 'resumable downloads', RANGE, 0, None),
            ('good/resume-checkpoint', 'resumes from a checkpoint', CHECKPOINT, 0, None),
            ('good/resume-offset', 'can be resumed', OFFSET, 0, None),
            ('good/noresume', 'not resumable', 'x = 1', 0, None),
            ('bad/resume', 'resumable downloads', 'x = 1', 2, 'resume'),
            ('bad/noresume', 'not resumable', RANGE, 2, 'no-resume'),
            ('bad/never', 'never resumable', CHECKPOINT, 2, 'no-resume'),
            ('nosource/resume', 'resumable', None, 2, 'resume'),
            ('nosource/noresume', 'not resumable', None, 0, None),
            ('fenced', '```text\nresumable\n```', None, 0, None),
            ('inline', '`resumable`', None, 2, 'resume'),
            ('wrapped', 'can be\nresumed', None, 2, 'resume'),
            ('bare-noun', 'checkpoint and restart and resume and progress', None, 0, None),
            ('not-evidence', 'resumable', 'for i in range(3):\n    f.seek(0)', 2, 'resume'),
        ]
        for label, doc, code, expected, family in cases:
            with self.subTest(label=label):
                result, data = self.fixture(doc, code)
                self.assertEqual(result, expected)
                self.assertEqual(data['findings'], [] if family is None else [dict(
                    rule='documented-{}-claim-false'.format(family), severity='HIGH',
                    path='README.md', claim=next(iter(claims(doc)))[1])])

    def test_testonly_thresholds(self):
        for threshold, expected in [('HIGH', 0), ('MEDIUM', 2)]:
            code, data = self.fixture('resumable', docpath='tests/docs/fixture.md', flags=['--fail-on', threshold])
            self.assertEqual(code, expected)
            self.assertEqual(data['findings'][0]['severity'], 'MEDIUM')

    def test_templates(self):
        for phrase in RESUME:
            self.assertEqual(list(claims(phrase)), [('resume', phrase)])
        for phrase in NORESUME:
            self.assertEqual(list(claims(phrase)), [('no-resume', phrase)])
        self.assertEqual(list(claims('supports-resuming')), [('resume', 'supports resuming')])
        self.assertEqual(list(claims('can be\n\nresumed')), [])

    def test_evidence_variants(self):
        for code in [RANGE, CHECKPOINT, OFFSET, 'task.resume()',
                     'h.setdefault("Range", f"bytes={offset}-")',
                     'h.update({"Range": "bytes=" + str(offset)})',
                     'request.headers["Range"] = "bytes=1-"',
                     'json.load(state_file)\njson.dump(data, state_file)',
                     'open(progress_path)\nopen(progress_path, "w")',
                     'open("x", "r+b")\nf.seek(1)\nPath("x").stat()']:
            with self.subTest(code=code):
                self.assertTrue(evidence(code))

    def test_non_evidence(self):
        for code in ['"Range bytes="', '# Range bytes=\nx=1',
                     'def resume(): pass', 'print("resume")',
                     'json.load(config_file)', 'open("x", "ab")',
                     'f.seek(1)', 'subprocess.run(["resume"])',
                     '"""Range bytes="""\nx=1']:
            with self.subTest(code=code):
                self.assertFalse(evidence(code))

    def test_multiple_and_project_wide(self):
        code, data = self.fixture('resumable and supports resume and not resumable', RANGE)
        self.assertEqual(len(data['findings']), 1)
        self.assertEqual(data['findings'][0]['rule'], 'documented-no-resume-claim-false')

    def test_ignore_and_source_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('resumable')
            (root / 'test_app.py').write_text(RANGE)
            (root / 'tests').mkdir()
            (root / 'tests' / 'app.py').write_text(RANGE)
            (root / '.env').write_bytes(b'\xff\x00')
            self.assertEqual(len(audit(root)), 1)
            self.assertEqual(audit(root, ['README.md']), [])
            (root / 'app.py').write_text(RANGE)
            self.assertEqual(audit(root), [])

    def test_usage(self):
        for args in [['--unknown'], ['--fail-on', 'LOW'], ['/nonexistent/audit/root']]:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                main(args)
            self.assertEqual(exc.exception.code, 1)

    def test_version_verbose(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exc:
            main(['--version'])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), '0.1.0')
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'README.md').write_text('resumable')
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([directory, '--verbose']), 2)
            self.assertIn('claim: resumable', output.getvalue())

    def test_self_audit(self):
        self.assertEqual(audit(Path(__file__).resolve().parents[1]), [])
