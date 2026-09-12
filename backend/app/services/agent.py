"""Phase 4 — Agent executor: Tool Selection → Execution → Response context."""
import re
from . import tools
from .. import models

def _note_from_text(text: str) -> tuple[str, str]:
    t = re.sub(r"\b(please )?(take a note|note (this|down|it)|save (this |as )?(a )?note)[:]?\s*", "", text, flags=re.I).strip()
    title = t[:60] if len(t) > 60 else (t.split("\n")[0][:60] if t else "Untitled")
    return title, t or text

def _event_from_text(text: str) -> tuple[str, str]:
    t = re.sub(r"\b(please )?(remind me (to )?|add (an? )?(event|reminder)[:]?\s*|schedule[:]?\s*)", "", text, flags=re.I).strip()
    due = ""
    m = re.search(r"\b(tomorrow|today|tonight|next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{4}-\d{2}-\d{2})\b", t, re.I)
    if m:
        due = m.group(1)
    return (t[:120] or text[:120]), due

def execute_plan(db, user_id: str, steps: list[dict]) -> tuple[list[dict], str]:
    """Returns (tool_calls, context_for_llm). tool_calls: [{tool, args, ok, result}]."""
    calls: list[dict] = []
    ctx_lines: list[str] = []
    for s in steps:
        tool, args = s["tool"], dict(s.get("args", {}))
        try:
            if tool == "calculator":
                r = tools.tool_calculator(args["expression"])
            elif tool == "notes_create":
                title, content = _note_from_text(args.get("text", ""))
                n = models.Note(user_id=user_id, title=title, content=content)
                db.add(n); db.commit(); db.refresh(n)
                r = {"ok": True, "result": {"id": n.id, "title": title}}
            elif tool == "notes_list":
                ns = db.query(models.Note).filter_by(user_id=user_id).order_by(models.Note.created_at.desc()).limit(20).all()
                r = {"ok": True, "result": [{"id": n.id, "title": n.title, "content": n.content[:200]} for n in ns]}
            elif tool == "calendar_create":
                title, due = _event_from_text(args.get("text", ""))
                e = models.CalendarEvent(user_id=user_id, title=title, due=due)
                db.add(e); db.commit(); db.refresh(e)
                db.add(models.Memory(user_id=user_id, kind="fact", content=f"Reminder: {title}" + (f" due {due}" if due else ""),
                                      importance="high", memory_type="long_term"))
                db.commit()
                r = {"ok": True, "result": {"id": e.id, "title": title, "due": due}}
            elif tool == "calendar_list":
                evs = db.query(models.CalendarEvent).filter_by(user_id=user_id).order_by(models.CalendarEvent.created_at.desc()).limit(20).all()
                r = {"ok": True, "result": [{"id": e.id, "title": e.title, "due": e.due, "done": e.done} for e in evs]}
            elif tool == "web_search":
                r = tools.tool_web_search(args.get("query", ""))
            elif tool == "file_list":
                r = tools.tool_list_files(user_id)
            elif tool == "file_read":
                r = tools.tool_read_file(user_id, args.get("name", ""))
            elif tool == "code_analyze":
                r = tools.tool_analyze_code(user_id, args.get("name", ""))
            elif tool == "goal_create":
                from . import knowledge_graph
                title = (args.get("title") or "").strip()[:200] or "Untitled goal"
                gl = models.Goal(user_id=user_id, title=title)
                db.add(gl); db.commit(); db.refresh(gl)
                knowledge_graph.backend_for(user_id).upsert("Goal", title)
                r = {"ok": True, "result": {"id": gl.id, "title": title}}
            elif tool == "goal_list":
                gs = db.query(models.Goal).filter_by(user_id=user_id).all()
                r = {"ok": True, "result": [
                    {"id": g.id, "title": g.title, "status": g.status, "progress": g.progress}
                    for g in gs]}
            elif tool == "goal_done":
                want = (args.get("title") or "").lower()
                gs = db.query(models.Goal).filter_by(user_id=user_id, status="active").all()
                hit = next((g for g in gs if want and (want in g.title.lower() or g.title.lower() in want)), None)
                if hit:
                    hit.status, hit.progress = "done", 100
                    db.commit()
                    r = {"ok": True, "result": {"id": hit.id, "title": hit.title, "status": "done"}}
                else:
                    r = {"ok": False, "result": f"no active goal matching '{args.get('title')}'"}
            elif tool == "goal_progress":
                want = (args.get("title") or "").lower()
                gs = db.query(models.Goal).filter_by(user_id=user_id).all()
                hit = next((g for g in gs if want and (want in g.title.lower() or g.title.lower() in want)), None)
                if hit:
                    hit.progress = max(0, min(100, int(args.get("progress", 0))))
                    if hit.progress >= 100:
                        hit.status = "done"
                    db.commit()
                    r = {"ok": True, "result": {"title": hit.title, "progress": hit.progress}}
                else:
                    r = {"ok": False, "result": f"no goal matching '{args.get('title')}'"}
            else:
                r = {"ok": False, "result": f"unknown tool: {tool}"}
        except Exception as e:
            r = {"ok": False, "result": str(e)}
        calls.append({"tool": tool, "args": args, "ok": r["ok"], "result": r["result"]})
        ctx_lines.append(f"[tool:{tool}] ok={r['ok']} → {str(r['result'])[:800]}")
    return calls, "\n".join(ctx_lines)
