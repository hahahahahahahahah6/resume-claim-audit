"""Static documentation auditor."""
import argparse
import ast
import fnmatch
import json
from pathlib import Path
import re
import sys

from . import __version__

RESUME = ('resumable', 'can be resumed', 'resumes from where it left off',
          'continues where it left off', 'picks up where it left off',
          'pick up where it left off', 'resume support', 'supports resume',
          'supports resuming', 'resumes from a checkpoint', 'resume from checkpoint')
NORESUME = ('not resumable', 'never resumable', 'cannot be resumed',
            'can not be resumed', 'no resume support', 'does not support resume',
            'does not support resuming', 'does not resume', 'never resumes',
            'must restart from scratch', 'must start over', 'restarts from scratch',
            'starts from scratch')
WORDS = ('checkpoint', 'state', 'progress', 'resume')


def prose(text):
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            lines.append('')
        elif fence is None:
            lines.append(line)
    return [re.sub(r'\s+', ' ', p.lower().replace('-', ' ')).strip()
            for p in re.split(r'\n\s*\n', '\n'.join(lines))]


def phrase_pattern(phrases):
    return re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in
                      sorted(phrases, key=len, reverse=True)) + r')(?!\w)')


def claims(text):
    for paragraph in prose(text):
        negative = phrase_pattern(NORESUME)
        for match in negative.finditer(paragraph):
            yield 'no-resume', match.group()
        remaining = negative.sub(lambda m: ' ' * len(m.group()), paragraph)
        for match in phrase_pattern(RESUME).finditer(remaining):
            yield 'resume', match.group()


def name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return name(node.value) + '.' + node.attr
    return ''


def literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return literal(node.left) + literal(node.right)
    if isinstance(node, ast.JoinedStr):
        return ''.join(literal(v) for v in node.values)
    return ''


def progress(node):
    value = literal(node) or name(node)
    return any(word in value.lower() for word in WORDS)


def evidence(source):
    tree = ast.parse(source)
    # Remove docstrings so they cannot contribute code literals.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                first = node.body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    node.body = node.body[1:]
    nodes = list(ast.walk(tree))
    range_key = False
    loaded = written = append = seek = size = False
    for node in nodes:
        if isinstance(node, ast.Dict):
            range_key |= any(literal(k) == 'Range' for k in node.keys if k)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            range_key |= literal(node.slice) == 'Range'
        if not isinstance(node, ast.Call):
            continue
        function = name(node.func)
        attr = node.func.attr if isinstance(node.func, ast.Attribute) else ''
        if attr == 'resume':
            return True
        if attr in ('update', 'setdefault') and node.args:
            range_key |= literal(node.args[0]) == 'Range'
        for keyword in node.keywords:
            if keyword.arg == 'headers':
                range_key |= literal(keyword.value) == 'Range'
        seek |= attr == 'seek'
        size |= function in ('os.path.getsize', 'os.stat') or attr == 'stat'
        if function in ('open', 'io.open') or attr == 'open':
            path = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'file'), None)
            mode = literal(node.args[1]) if len(node.args) > 1 else 'r'
            mode = next((literal(k.value) for k in node.keywords if k.arg == 'mode'), mode)
            append |= mode in ('ab', 'a+', 'r+b')
            if path is not None and progress(path):
                loaded |= 'r' in mode or '+' in mode
                written |= any(c in mode for c in 'wa+')
        if function in ('json.load', 'pickle.load', 'yaml.safe_load', 'json.dump', 'pickle.dump'):
            index = 1 if function.endswith('.dump') else 0
            if len(node.args) > index and progress(node.args[index]):
                if function.endswith('.dump'):
                    written = True
                else:
                    loaded = True
    byte_value = any('bytes=' in literal(n) for n in nodes)
    return (range_key and byte_value) or (loaded and written) or (append and seek and size)


def audit(root, ignores=()):
    root = Path(root)
    files = sorted(p for p in root.rglob('*') if p.is_file() and
                   not any(fnmatch.fnmatch(p.relative_to(root).as_posix(), g) for g in ignores))
    found_evidence = False
    errors = []
    for path in files:
        rel = path.relative_to(root)
        if path.suffix == '.py' and 'tests' not in rel.parts and not path.name.startswith('test_'):
            try:
                found_evidence |= bool(evidence(path.read_text(encoding='utf-8')))
            except (SyntaxError, UnicodeError, OSError) as exc:
                errors.append('{}: {}'.format(rel.as_posix(), exc))
    if errors:
        raise ValueError('; '.join(errors))
    findings = {}
    for path in files:
        rel = path.relative_to(root)
        is_doc = rel.as_posix() == 'README.md' or (path.suffix == '.md' and 'docs' in rel.parts)
        if not is_doc:
            continue
        for family, phrase in claims(path.read_text(encoding='utf-8')):
            false = not found_evidence if family == 'resume' else found_evidence
            if false:
                severity = 'MEDIUM' if 'tests' in rel.parts else 'HIGH'
                item = dict(rule='documented-{}-claim-false'.format(family), severity=severity,
                            path=rel.as_posix(), claim=phrase)
                if family not in findings or (findings[family]['severity'] == 'MEDIUM' and severity == 'HIGH'):
                    findings[family] = item
    return list(findings.values())


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, '{}: error: {}\n'.format(self.prog, message))


def main(argv=None):
    parser = Parser(prog='resume-claim-audit')
    parser.add_argument('root', nargs='?', default='.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', nargs='+', action='extend', default=[])
    parser.add_argument('--fail-on', choices=('HIGH', 'MEDIUM'), default='HIGH')
    args = parser.parse_args(argv)
    if not Path(args.root).is_dir():
        parser.error('ROOT must be an existing directory')
    try:
        findings = audit(args.root, args.ignore)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    failed = any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in findings)
    status = 'FAIL' if failed else 'PASS'
    if args.json:
        print(json.dumps(dict(status=status, findings=findings)))
    else:
        print(status)
        for finding in findings:
            print('{severity} {rule} {path}'.format(**finding))
            if args.verbose:
                print('  claim: ' + finding['claim'])
    return 2 if failed else 0
