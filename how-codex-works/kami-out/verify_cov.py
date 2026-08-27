
import json, re
from pathlib import Path

out = Path("/Users/ahuamao/Documents/Codes/codex/how-codex-works/kami-out")
data = json.loads((out / "content.json").read_text(encoding="utf-8"))
html = (out / "01-overview.html").read_text(encoding="utf-8")

# normalize like the checker does (CJK: strip all whitespace)
plain = re.sub(r"\s+", "", html)

missing = []
checked = 0
def walk(node, path):
    global checked
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]")
    else:
        s = str(node).strip()
        if not s or len(s) > 80:
            return
        checked += 1
        if re.sub(r"\s+", "", s) not in plain:
            missing.append(f"{path}: {s!r}")

walk(data["content"], "content")

print("checked atomic values:", checked)
print("missing:", len(missing))
for m in missing[:40]:
    print("  ", m)

# also confirm a few key exact paths/digits
for token in ["codex-rs/core/src/codex_thread.rs", "codex-rs/core/src/agent/control.rs", "134", "#37871", "ExtensionRegistry", "exec-server", "AppServerThread"]:
    print(f"  token {token!r} present:", re.sub(r"\s+", "", token) in plain)
