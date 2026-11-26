"""Freeze/verify current source, local evidence and clean deployment package hashes."""
from pathlib import Path
import argparse
import hashlib

root=Path(__file__).resolve().parents[1]
manifest=root/"BASELINE_SOURCE_MANIFEST_V3.sha256"
parser=argparse.ArgumentParser()
parser.add_argument("--write",action="store_true")
args=parser.parse_args()
if args.write:
    files=set()
    for name in ("src","tests","scripts","verification/final"):
        files.update(p for p in (root/name).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix not in {".pyc",".pyo"})
    files.update(p for p in root.iterdir() if p.is_file() and
                 (p.suffix in {".md",".yaml",".toml",".ini",".txt",".sha256"} or p.name==".gitignore"))
    files.update(p for p in (root/".aws-sam/final-build").rglob("*") if p.is_file())
    files.discard(manifest)
    lines=["# SilentLoss final corrected baseline V3 - 2026-11-26",
           "# Unsigned local integrity snapshot, not historical authenticity or AWS execution proof.",
           "# V1/V2 preserved as historical inputs. Caches/environments/previous builds excluded."]
    for path in sorted(files,key=lambda p:p.relative_to(root).as_posix()):
        lines.append(hashlib.sha256(path.read_bytes()).hexdigest()+"  "+path.relative_to(root).as_posix())
    manifest.write_text("\n".join(lines)+"\n",encoding="utf-8")
entries=0
for line in manifest.read_text().splitlines():
    if not line or line.startswith("#"): continue
    expected,relative=line.split("  ",1)
    path=(root/relative).resolve()
    assert path.is_relative_to(root),"Manifest path outside workspace"
    assert path.is_file(),relative
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected,relative
    entries+=1
print(f"MANIFEST PASS: {entries} files match")
