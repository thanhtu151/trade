"""Sinh research/tools/REPO_MAP.md: mỗi module .py (dòng, docstring 1 dòng, class/hàm + dòng) và module -> test.
Chỉ đọc mã nguồn bằng ast; chỉ ghi REPO_MAP.md. Dùng: python3 research/tools/repo_map.py"""
import ast, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("REPO_MAP.md")
SKIP = {".venv", "venv", "__pycache__", "secrets", ".github", ".git", "node_modules", "research", "tests"}
MAX_LINES = 400


def skip(p):
    return any(part in SKIP or part.startswith(".env") for part in p.relative_to(ROOT).parts[:-1]) \
        or p.name.startswith(".env")


def summarize(p):
    src = p.read_text(encoding="utf-8", errors="replace")
    n = src.count("\n") + 1
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return n, "(lỗi cú pháp)", []
    doc = (ast.get_docstring(tree) or "").strip().split("\n")[0][:90]
    items = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append(f"{node.name}:{node.lineno}")
        elif isinstance(node, ast.ClassDef):
            items.append(f"class {node.name}:{node.lineno}")
    return n, doc, items


def imports(p):
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def main():
    mods = sorted(p for p in ROOT.rglob("*.py") if not skip(p))
    names = {p.stem for p in mods}
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    t2m = {}
    for t in tests:
        used = (imports(t) & names) | {m for m in names if re.search(r"\b" + re.escape(m) + r"\b", t.stem)}
        t2m[t.name] = sorted(used)
    lines = ["# REPO MAP (tự sinh bởi research/tools/repo_map.py — chạy lại khi code đổi)", ""]
    lines.append("Định dạng: `hàm:dòng_bắt_đầu` (cấp module). Đọc bằng Read offset/limit quanh số dòng.\n")
    for p in mods:
        n, doc, items = summarize(p)
        rel = p.relative_to(ROOT).as_posix()
        tt = [t for t, ms in t2m.items() if p.stem in ms]
        lines.append(f"## {rel} ({n} dòng) — {doc or 'không docstring'}")
        if items:
            lines.append(", ".join(items))
        if tt:
            lines.append("test: " + ", ".join(tt))
        lines.append("")
    lines.append("## Test -> module (import/tên)")
    for t, ms in t2m.items():
        lines.append(f"- {t}: {', '.join(ms) or '-'}")
    if len(lines) > MAX_LINES:
        # rút gọn: bỏ danh sách hàm dài, chỉ giữ 12 mục đầu mỗi module
        out = []
        for l in lines:
            if l and not l.startswith(("#", "test:", "- ", "Định")) and ":" in l and l.count(", ") > 12:
                l = ", ".join(l.split(", ")[:12]) + ", …"
            out.append(l)
        lines = out
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES - 1] + ["… (cắt bớt cho ≤400 dòng)"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(lines)} dòng, {len(mods)} module)")


if __name__ == "__main__":
    main()
