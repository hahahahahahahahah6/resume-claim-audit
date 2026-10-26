# resume-claim-audit

An offline, standard-library-only Python 3.9+ CLI that compares documented
interruption recovery promises with static Python source evidence.

Install and run:

```sh
python3 -m pip install .
resume-claim-audit /path/to/project --verbose
python3 -m resume_claim_audit /path/to/project --json
python3 -m unittest discover -s tests
python3 -m build
```

The optional root defaults to the current directory. Use `--ignore GLOB ...`
to exclude relative paths, `--fail-on HIGH|MEDIUM` to set the failure threshold
(default HIGH), and `--version` to print 0.1.0. Exit codes are 0 for PASS,
2 for findings meeting the threshold, and 1 for usage or input errors.
JSON output contains status and findings; verbose text includes the matched
phrase. Each family produces at most one finding. Rules are:

```text
documented-resume-claim-false
documented-no-resume-claim-false
```

Root README.md and Markdown below docs directories are scanned. Fenced blocks
are excluded; inline code counts. Text is lowercased, hyphens become spaces,
and whitespace within paragraphs is collapsed. Negative spans are removed
before positive matching. The complete phrase inventory follows:

```text
resumable
can be resumed
resumes from where it left off
continues where it left off
picks up where it left off
pick up where it left off
resume support
supports resume
supports resuming
resumes from a checkpoint
resume from checkpoint

not resumable
never resumable
cannot be resumed
can not be resumed
no resume support
does not support resume
does not support resuming
does not resume
never resumes
must restart from scratch
must start over
restarts from scratch
starts from scratch
```

Source checks use ASTs, skipping tests directories and test_*.py files.
Evidence includes a Range key paired with a byte-range value in one module,
persisted checkpoint/state/progress file reads and writes, an append-capable
file open paired with seeking and a size check, or an attribute call named
resume. Comments and docstrings do not qualify. Findings are HIGH, or MEDIUM
for documents under tests directories. An unsupported positive promise fails
even without source; a negative statement without source passes. Files named
.env are never opened. The tool performs no network access or tree writes.
Unreadable or invalid Python input produces an input error.

Compared with retry/backup/idempotency/cache-claim-audit, this tool checks
continuation after interruption: retry repeats an operation, backup preserves
a copy, idempotency concerns safe repetition, and cache avoids recomputation.
Those mechanisms alone provide no continuation evidence.

Limitations: matching uses exact phrases, not semantics. Static evidence is
an approximation: a Range header and byte-range value cannot prove that a
server honors the request; checkpoint reads and writes cannot prove that
saved progress is restored correctly. SQLite and progress-table schemes are
not detected. Evidence is project-wide rather than per-feature. There is no
generic negation analysis beyond the listed negative templates and span
removal. A syntactically valid call can qualify even if unreachable.

Licensed under MIT; see LICENSE.
