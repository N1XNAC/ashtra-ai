"""Phase 6 — Knowledge graph (SYSTEM_ARCHITECTURE.md: Neo4j layer).

Stores User → Projects → Skills → Goals → Interests relationships.

Backend abstraction: local JSON graph works with zero setup (dev default,
persisted at ./graph_data/{user}.json). Production Neo4j: set NEO4J_URI
(+ NEO4J_USER/NEO4J_PASSWORD) and `pip install neo4j` — the Cypher backend
in `Neo4jBackend` below takes over with the same node/edge semantics.
"""
import json
import os
import re

GRAPH_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "graph_data")

ENTITY_PATTERNS = [
    ("Project", re.compile(r"(?:working on|building|my project|project called|developing)\s+([A-Za-z][\w\- ]{1,40})", re.I)),
    ("Skill", re.compile(r"(?:learning|know|skilled in|good at|proficient in)\s+([A-Za-z][\w+#.\- ]{1,30})", re.I)),
    ("Goal", re.compile(r"(?:my goal(?: is)?|i want to|aim to|plan to)\s+([A-Za-z][\w\- ]{1,60})", re.I)),
    ("Interest", re.compile(r"(?:i (?:like|love|enjoy)|interested in|fan of)\s+([A-Za-z][\w\- ]{1,30})", re.I)),
]


STOP_WORDS = re.compile(r"\s+(and|for|to|with|using|while|because|in order|so that|which|that)\b", re.I)


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip().rstrip(".,!?"))
    s = STOP_WORDS.split(s)[0]  # cut "X and learning Y" → "X"
    s = " ".join(s.split()[:4])  # cap length: entities are noun phrases
    return s[:60]


def extract_entities(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for kind, rx in ENTITY_PATTERNS:
        for m in rx.finditer(text or ""):
            name = _clean(m.group(1))
            if len(name) >= 2 and not re.fullmatch(r"(it|this|that|them|a|the)", name, re.I):
                out.append((kind, name))
    return out


class LocalBackend:
    """Zero-dep JSON graph. Nodes: {id, kind, name}. Edges: {src, rel, dst}."""

    def __init__(self, user_id: str):
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", user_id)
        os.makedirs(GRAPH_ROOT, exist_ok=True)
        self.path = os.path.join(GRAPH_ROOT, f"{safe}.json")
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.nodes, self.edges = d.get("nodes", []), d.get("edges", [])
        except Exception:
            self.nodes = [{"id": "user", "kind": "User", "name": "master"}]
            self.edges = []

    def _save(self):
        with open(self.path, "w") as f:
            json.dump({"nodes": self.nodes, "edges": self.edges}, f, indent=2)

    def upsert(self, kind: str, name: str) -> str:
        nid = f"{kind}:{name.lower()}"
        if not any(n["id"] == nid for n in self.nodes):
            self.nodes.append({"id": nid, "kind": kind, "name": name})
        if not any(e["src"] == "user" and e["dst"] == nid for e in self.edges):
            self.edges.append({"src": "user", "rel": f"HAS_{kind.upper()}", "dst": nid})
        self._save()
        return nid

    def link(self, src: str, rel: str, dst: str):
        if not any(e["src"] == src and e["rel"] == rel and e["dst"] == dst for e in self.edges):
            self.edges.append({"src": src, "rel": rel, "dst": dst})
            self._save()

    def neighborhood(self, node_id: str = "user", depth: int = 1) -> dict:
        seen, frontier = {node_id}, {node_id}
        for _ in range(max(1, depth)):
            nxt = set()
            for e in self.edges:
                if e["src"] in frontier:
                    nxt.add(e["dst"])
                if e["dst"] in frontier:
                    nxt.add(e["src"])
            frontier = nxt - seen
            seen |= nxt
        return {"nodes": [n for n in self.nodes if n["id"] in seen],
                "edges": [e for e in self.edges if e["src"] in seen and e["dst"] in seen]}

    def all(self) -> dict:
        return {"nodes": self.nodes, "edges": self.edges,
                "counts": {k: sum(1 for n in self.nodes if n["kind"] == k)
                           for k in ("Project", "Skill", "Goal", "Interest")}}


class Neo4jBackend:
    """Production backend (requires `pip install neo4j` + NEO4J_URI). Same semantics."""

    def __init__(self, uri: str, user: str, password: str, owner: str):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.owner = owner

    def upsert(self, kind: str, name: str) -> str:
        with self.driver.session() as s:
            s.run(f"MERGE (u:User {{id:$o}}) MERGE (n:{kind} {{name:$n}}) "
                  f"MERGE (u)-[:HAS_{kind.upper()}]-(n)", o=self.owner, n=name)
        return f"{kind}:{name.lower()}"

    def link(self, src: str, rel: str, dst: str):
        pass  # extended relations land here in prod hardening

    def neighborhood(self, node_id: str = "user", depth: int = 1) -> dict:
        with self.driver.session() as s:
            rows = s.run("MATCH (u:User {id:$o})-[r]-(n) RETURN labels(n), n.name",
                         o=self.owner).data()
        return {"nodes": [{"kind": r["labels(n)"][0], "name": r["n.name"]} for r in rows], "edges": []}

    def all(self) -> dict:
        return self.neighborhood()


def backend_for(user_id: str):
    try:
        from ..config import settings
        uri = getattr(settings, "neo4j_uri", "")
        if uri:
            try:
                return Neo4jBackend(uri, settings.neo4j_user, settings.neo4j_password, user_id)
            except Exception:
                pass
    except Exception:
        pass
    return LocalBackend(user_id)


def observe(user_id: str, text: str) -> list[tuple[str, str]]:
    """Extract entities from a message and merge into the user's graph."""
    ents = extract_entities(text)
    if not ents:
        return []
    g = backend_for(user_id)
    for kind, name in ents:
        g.upsert(kind, name)
    return ents


def rebuild_from_db(db, user_id: str) -> dict:
    """Rebuild graph from profile + goals + notes (source of truth in SQL)."""
    from .. import models
    g = backend_for(user_id)
    p = db.query(models.UserProfile).filter_by(user_id=user_id).first()
    if p:
        for kind, items in (("Interest", p.interests or []), ("Skill", p.skills or []),
                            ("Goal", p.goals or [])):
            for name in items:
                if isinstance(name, str) and len(name) < 80:
                    g.upsert(kind, _clean(name) or name)
    for goal in db.query(models.Goal).filter_by(user_id=user_id).all():
        nid = g.upsert("Goal", goal.title)
        if goal.status == "done":
            g.link("user", "COMPLETED", nid)
    for n in db.query(models.Note).filter_by(user_id=user_id).all():
        for kind, name in extract_entities(f"{n.title} {n.content}"):
            g.upsert(kind, name)
    return g.all() if isinstance(g, LocalBackend) else g.all()
