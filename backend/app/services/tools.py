"""Phase 4 — Tool implementations (AI_AGENT_SPECIFICATION.txt).

Tools: calculator, notes, files, code analysis, web search, calendar.
All tools are user-scoped, sandboxed, dependency-free (no keys needed).
Each returns {"ok": bool, "result": ...} — planner-ready.
"""
import ast
import math
import os
import re
from datetime import datetime

UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")

# ---------- calculator ----------
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)
_ALLOWED_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max,
                  "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log,
                  "pow": pow, "pi": math.pi, "e": math.e}

def calc_eval(expr: str):
    node = ast.parse(expr, mode="eval").body
    return _eval(node)

def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        l, r = _eval(node.left), _eval(node.right)
        return {ast.Add: l + r, ast.Sub: l - r, ast.Mult: l * r, ast.Div: l / r,
                ast.FloorDiv: l // r, ast.Mod: l % r, ast.Pow: l ** r}[type(node.op)]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
        v = _eval(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS:
        fn = _ALLOWED_FUNCS[node.func.id]
        return fn(*[_eval(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in ("pi", "e"):
        return _ALLOWED_FUNCS[node.id]
    raise ValueError("unsupported expression")

def tool_calculator(expression: str) -> dict:
    try:
        return {"ok": True, "result": calc_eval(expression.strip())}
    except Exception as e:
        return {"ok": False, "result": f"calc error: {e}"}

# ---------- files (sandboxed to ./uploads/{user_id}/) ----------
def _user_dir(user_id: str) -> str:
    d = os.path.join(UPLOAD_ROOT, re.sub(r"[^a-zA-Z0-9_-]", "_", user_id))
    os.makedirs(d, exist_ok=True)
    return d

def _safe_path(user_id: str, name: str) -> str:
    base = os.path.basename(name)
    return os.path.join(_user_dir(user_id), base)

def tool_list_files(user_id: str) -> dict:
    try:
        files = sorted(os.listdir(_user_dir(user_id)))
        return {"ok": True, "result": files}
    except Exception as e:
        return {"ok": False, "result": str(e)}

def tool_read_file(user_id: str, name: str, max_chars: int = 8000) -> dict:
    try:
        p = _safe_path(user_id, name)
        if not os.path.isfile(p):
            return {"ok": False, "result": f"file not found: {name}"}
        if os.path.getsize(p) > 2_000_000:
            return {"ok": False, "result": "file too large (>2MB)"}
        with open(p, "r", errors="replace") as f:
            return {"ok": True, "result": f.read(max_chars)}
    except Exception as e:
        return {"ok": False, "result": str(e)}

def tool_analyze_code(user_id: str, name: str) -> dict:
    """AST analysis for .py files: functions, classes, imports, LOC."""
    r = tool_read_file(user_id, name, max_chars=500_000)
    if not r["ok"]:
        return r
    src = r["result"]
    info = {"lines": len(src.splitlines()), "chars": len(src)}
    if not name.endswith(".py"):
        info["note"] = "non-python file: stats only"
        return {"ok": True, "result": info}
    try:
        tree = ast.parse(src)
    except Exception as e:
        return {"ok": True, "result": {**info, "parse_error": str(e)}}
    info["functions"] = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    info["classes"] = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    info["imports"] = sorted({(n.module or "") if isinstance(n, ast.ImportFrom) else a.name
                              for n in ast.walk(tree) for a in getattr(n, "names", [])
                              if isinstance(n, (ast.Import, ast.ImportFrom))})
    return {"ok": True, "result": info}

# ---------- web search (keyless DuckDuckGo Instant Answer) ----------
def tool_web_search(query: str) -> dict:
    try:
        import httpx
        r = httpx.get("https://api.duckduckgo.com/",
                      params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                      timeout=15, headers={"User-Agent": "AshtraAI/1.0"})
        r.raise_for_status()
        j = r.json()
        out = {"answer": j.get("AbstractText", ""), "source": j.get("AbstractURL", ""),
               "related": [t.get("Text", "") for t in (j.get("RelatedTopics") or [])][:5]}
        if not out["answer"] and not out["related"]:
            out["answer"] = f"No instant answer for '{query}'. (Phase 4: keyless DDG only.)"
        return {"ok": True, "result": out}
    except Exception as e:
        return {"ok": False, "result": f"search error: {e}"}

# ---------- reminders helper (automation lite) ----------
def due_reminders(db, user_id: str) -> list[str]:
    from .. import models
    evs = db.query(models.CalendarEvent).filter_by(user_id=user_id, done=False).all()
    return [f"{e.title}" + (f" (due {e.due})" if e.due else "") for e in evs][:5]

def stamp() -> str:
    return datetime.utcnow.strftime("%Y-%m-%d %H:%M UTC")
