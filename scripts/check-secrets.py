#!/usr/bin/env python3
import re, subprocess, sys
from pathlib import Path

FILES=subprocess.check_output(["git","ls-files","-z"]).decode().split("\0")
FILES=[f for f in FILES if f]
PATTERNS={
 "Spotify API key":re.compile(r"spak_[A-Za-z0-9_-]{12,}"),
 "AWS access key":re.compile(r"AKIA[0-9A-Z]{16}"),
 "Google API key":re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
 "GitHub token":re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
 "OpenAI-style key":re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
 "Private key":re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
PLACEHOLDERS={"","change-me","changeme","example","placeholder","your-key-here","xxx","none","null"}
LITERAL=re.compile(r'''(?ix)
(api[_-]?key|client[_-]?secret|password|access[_-]?token|refresh[_-]?token)
\s*[:=]\s*(["'])([^"'\r\n]+)\2
''')

def scan_text(name,text,where):
    hits=[]
    for label,rx in PATTERNS.items():
        if rx.search(text): hits.append((name,f"{label} [{where}]"))
    if Path(name).name != ".env.example":
        for m in LITERAL.finditer(text):
            value=m.group(3).strip().lower()
            if len(value)>=8 and value not in PLACEHOLDERS:
                factory_default = (name == "core/app/userauth.py" and value == "password")
                if not factory_default and not value.startswith(("${","os.getenv","config.get","settings.get")):
                    hits.append((name,f"literal credential assignment [{where}]"))
                    break
    return hits

bad=[]
for name in FILES:
    p=Path(name)
    try: text=p.read_text(errors="strict")
    except (UnicodeDecodeError,OSError): continue
    bad += scan_text(name,text,"working tree")
    try:
        raw=subprocess.check_output(["git","show",f"HEAD:{name}"],stderr=subprocess.DEVNULL)
        committed=raw.decode("utf-8")
    except (subprocess.CalledProcessError,UnicodeDecodeError):
        continue
    bad += scan_text(name,committed,"HEAD")

if bad:
    print("SECRET CHECK FAILED",file=sys.stderr)
    for name,label in sorted(set(bad)):
        print(f"  {name}: {label}",file=sys.stderr)
    sys.exit(1)
print(f"secret check OK: {len(FILES)} tracked files + HEAD")
