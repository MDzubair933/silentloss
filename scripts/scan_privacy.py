"""Heuristic local source/artifact scan; report locations only, never matched values."""
import json
from pathlib import Path
import re

root=Path(__file__).resolve().parents[1]
roots=[root/p for p in ("src","scripts","tests","verification/final",".aws-sam/final-build")]
files={p for directory in roots for p in directory.rglob("*") if p.is_file()
       and p.suffix in {".py",".json",".jsonl",".yaml",".yml",".txt",".toml",".ini"}
       and "__pycache__" not in p.parts}
files.update(p for p in root.iterdir() if p.is_file() and p.suffix in {".md",".yaml",".json",".toml",".ini",".txt"})
patterns={"access_key":r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
          "private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
          "account_arn":r"arn:[^\s\"']*?:([0-9]{12}):",
          "account_endpoint":r"amazonaws\.com/([0-9]{12})/"}
findings=[]
for path in files:
    data=path.read_text(encoding="utf-8",errors="replace")
    for category,pattern in patterns.items():
        for match in re.finditer(pattern,data):
            if category.startswith("account_") and match.group(1) in {"123456789012","000000000000"}:
                continue  # Explicit synthetic test fixture identities.
            findings.append({"file":path.relative_to(root).as_posix(),"line":data[:match.start()].count("\n")+1,"category":category})
print(json.dumps({"files_scanned":len(files),"nonfixture_findings":findings,
                  "scope":"Local source, root documents/configuration, final evidence and final deployment packages; not a proof that arbitrary secrets cannot exist"},indent=2))
raise SystemExit(bool(findings))
