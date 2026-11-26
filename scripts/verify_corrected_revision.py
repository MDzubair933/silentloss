"""Verify the corrected local revision without rewriting historical manifests."""
import argparse
import hashlib
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
evidence = root / "evidence/phase-08-correction"
snapshot = evidence / "benchmarked-revision"
manifest = root / "CORRECTED_REVISION_MANIFEST.sha256"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--write", action="store_true", help="Freeze current correction files after checks pass")
args = parser.parse_args()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def entries(path):
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line and not line.startswith("#"):
            yield line.split("  ", 1)

# Preserve the old root manifest and every file it describes at its historical bytes.
old = root / "PHASE_6_7_EVIDENCE_MANIFEST.sha256"
assert old.read_bytes() == (snapshot / old.name).read_bytes()
at_original_path, at_snapshot, failures = 0, [], []
for expected, relative in entries(old):
    current = root / relative
    preserved = snapshot / relative
    if current.is_file() and sha(current) == expected:
        at_original_path += 1
    elif preserved.is_file() and sha(preserved) == expected:
        at_snapshot.append(relative)
    else:
        failures.append(relative)
assert not failures, failures
for expected, relative in entries(evidence / "benchmarked-revision.sha256"):
    assert sha(root / relative) == expected, relative
print(json.dumps({"historical_phase_6_7_entries": at_original_path + len(at_snapshot),
                  "matching_original_paths": at_original_path,
                  "corrected_paths_preserved_in_snapshot": at_snapshot,
                  "historical_failures": failures}, indent=2))

files = set()
for name in ("src", "tests", "scripts", ".aws-sam/corrected-build", "evidence/phase-08-correction"):
    files.update(p for p in (root / name).rglob("*") if p.is_file()
                 and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo"))
files.update(root / name for name in ("template.yaml", "pytest.ini", "requirements.txt", "requirements-dev.txt",
                                     "FINAL_TECHNICAL_VERIFICATION.md", "PUBLICATION_CLAIMS_MATRIX.md"))
files.update(root.glob("*.sha256"))
files.discard(manifest)

# Inspect corrected distributable code/reports/evidence only. Existing private AWS
# exports elsewhere remain private; they are not silently redacted or copied here.
patterns = {"access_key": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
            "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            "account_arn": r"arn:[^\s\"']*?:([0-9]{12}):",
            "account_endpoint": r"amazonaws\.com/([0-9]{12})/"}
privacy = []
for path in files:
    if path.suffix not in {".py", ".json", ".jsonl", ".yaml", ".md", ".txt"}:
        continue
    data = path.read_text(encoding="utf-8", errors="replace")
    for category, pattern in patterns.items():
        for match in re.finditer(pattern, data):
            if category.startswith("account_") and match.group(1) in {"123456789012", "000000000000"}:
                continue
            privacy.append({"file": path.relative_to(root).as_posix(), "category": category,
                            "line": data[:match.start()].count("\n") + 1})
assert not privacy, privacy  # Print locations only; never print matched values.
print("Corrected revision privacy scan PASS (heuristic; synthetic fixture identities excluded)")

if args.write:
    lines = ["# SilentLoss corrected revision after Phase 8; no AWS deployment or live benchmark",
             "# Historical measurements apply to the benchmarked revision, not this correction.",
             "# Unsigned local integrity snapshot; excludes this manifest itself and mutable tool caches."]
    lines.extend(sha(p) + "  " + p.relative_to(root).as_posix()
                 for p in sorted(files, key=lambda p: p.relative_to(root).as_posix()))
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
if manifest.exists():
    count = 0
    for expected, relative in entries(manifest):
        path = (root / relative).resolve()
        assert path.is_relative_to(root) and path.is_file(), relative
        assert sha(path) == expected, relative
        count += 1
    print(f"CORRECTED REVISION MANIFEST PASS: {count} files")
