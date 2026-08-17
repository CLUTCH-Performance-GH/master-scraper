"""Entity-resolution graph — the feature that turns a scraper into OSINT software.

Commercial tools (Maltego, SpiderFoot) are, at heart, a graph that links
people ↔ companies ↔ domains ↔ locations ↔ filings into one connected picture.
This is that graph, SQLite-backed, with deterministic resolution by normalized
key and optional Claude-assisted fuzzy matching for the hard cases.

    g = EntityGraph("acme_intel")
    co = g.add("company", "Maple Scapes LLC", website="maplescapes.com")
    p  = g.add("person", "Jane Doe", title="Owner")
    g.link(p, co, "works_at")
    g.link(co, g.add("domain", "maplescapes.com"), "owns_domain")
    g.to_html("output/acme_graph.html")   # interactive link chart

Resolution: add() normalizes the name + type into a key; an existing entity
with the same key is reused (and attributes merged) rather than duplicated.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import settings
from .extract import extract_domain, normalize_company

# Normalizers per entity type → the dedup key.
_NORMALIZERS = {
    "company": normalize_company,
    "person": lambda s: " ".join(s.lower().split()),
    "domain": extract_domain,
    "location": lambda s: " ".join(s.lower().replace(",", " ").split()),
    "filing": lambda s: s.lower().strip(),
    "product": lambda s: " ".join(s.lower().split()),
}


@dataclass
class Entity:
    id: int
    type: str
    name: str
    attrs: dict


class EntityGraph:
    def __init__(self, name: str, db_path: Optional[Path] = None):
        self.name = name
        self.db_path = db_path or (settings.PROJECT_ROOT / "data" / f"graph_{name}.sqlite3")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL,
                attrs TEXT NOT NULL, UNIQUE(type, key));
            CREATE TABLE IF NOT EXISTS edges (
                src INTEGER NOT NULL, dst INTEGER NOT NULL, rel TEXT NOT NULL,
                attrs TEXT NOT NULL DEFAULT '{}', UNIQUE(src, dst, rel));
            CREATE INDEX IF NOT EXISTS idx_edge_src ON edges(src);
            CREATE INDEX IF NOT EXISTS idx_edge_dst ON edges(dst);
        """)
        self._conn.commit()

    def _key(self, etype: str, name: str) -> str:
        return _NORMALIZERS.get(etype, lambda s: s.lower().strip())(name)

    # -- add / merge ----------------------------------------------------------

    def add(self, etype: str, name: str, **attrs) -> int:
        """Add or merge an entity; returns its id. Same (type, normalized-name)
        is reused and its attributes merged (new non-empty values win)."""
        key = self._key(etype, name)
        if not key:
            raise ValueError(f"empty key for {etype!r} name {name!r}")
        attrs = {k: v for k, v in attrs.items() if v not in (None, "")}
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, attrs FROM entities WHERE type=? AND key=?",
                (etype, key)).fetchone()
            if row:
                eid, existing_name, existing_attrs = row
                merged = {**json.loads(existing_attrs), **attrs}
                best_name = name if len(name) > len(existing_name) else existing_name
                self._conn.execute("UPDATE entities SET name=?, attrs=? WHERE id=?",
                                   (best_name, json.dumps(merged), eid))
                self._conn.commit()
                return eid
            cur = self._conn.execute(
                "INSERT INTO entities (type, key, name, attrs) VALUES (?,?,?,?)",
                (etype, key, name, json.dumps(attrs)))
            self._conn.commit()
            return cur.lastrowid

    def add_row(self, row: dict, etype: str = "company") -> int:
        """Convenience: ingest a scraped row, wiring company↔domain↔location↔person."""
        name = row.get("company_name") or row.get("name", "")
        eid = self.add(etype, name, phone=row.get("phone", ""),
                       website=row.get("website", ""), state=row.get("state", ""),
                       source=row.get("source", ""))
        domain = extract_domain(row.get("website", ""))
        if domain:
            self.link(eid, self.add("domain", domain), "owns_domain")
        street = row.get("street", "")
        if street:
            loc = f"{street} {row.get('city','')} {row.get('state','')} {row.get('zip','')}".strip()
            self.link(eid, self.add("location", loc, **{k: row.get(k, "")
                      for k in ("city", "state", "zip")}), "located_at")
        contact = row.get("contact_name", "")
        if contact:
            pid = self.add("person", contact, title=row.get("title", ""),
                           email=row.get("email", ""), linkedin=row.get("linkedin", ""))
            self.link(pid, eid, "works_at")
        return eid

    def link(self, src: int, dst: int, rel: str, **attrs) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO edges (src, dst, rel, attrs) VALUES (?,?,?,?)",
                (src, dst, rel, json.dumps(attrs)))
            self._conn.commit()

    # -- query ----------------------------------------------------------------

    def get(self, eid: int) -> Optional[Entity]:
        row = self._conn.execute(
            "SELECT id, type, name, attrs FROM entities WHERE id=?", (eid,)).fetchone()
        return Entity(row[0], row[1], row[2], json.loads(row[3])) if row else None

    def neighbors(self, eid: int) -> list[tuple[str, Entity]]:
        """(relationship, entity) pairs touching eid in either direction."""
        out = []
        for src, dst, rel in self._conn.execute(
                "SELECT src, dst, rel FROM edges WHERE src=? OR dst=?", (eid, eid)):
            other = self.get(dst if src == eid else src)
            if other:
                out.append((rel, other))
        return out

    def find(self, etype: str = "", name: str = "") -> list[Entity]:
        q, args = "SELECT id, type, name, attrs FROM entities WHERE 1=1", []
        if etype:
            q += " AND type=?"; args.append(etype)
        if name:
            q += " AND key=?"; args.append(self._key(etype or "company", name))
        return [Entity(*r[:3], json.loads(r[3]))
                for r in self._conn.execute(q, args)]

    def stats(self) -> dict:
        ents = dict(self._conn.execute(
            "SELECT type, COUNT(*) FROM entities GROUP BY type").fetchall())
        edges = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {"entities": ents, "edges": edges}

    # -- fuzzy resolution (optional, Claude-assisted) -------------------------

    def resolve_fuzzy(self, etype: str, claude, threshold_pairs: int = 200) -> int:
        """Find near-duplicate entities of one type that the deterministic key
        missed ("Maple Scapes" vs "Maple Scape Inc of Bend") and merge them.
        Uses Claude to judge candidate pairs. Returns the number merged."""
        ents = self.find(etype)
        if len(ents) < 2:
            return 0
        # Block candidates by shared first token to keep pair count sane.
        from collections import defaultdict
        blocks = defaultdict(list)
        for e in ents:
            tok = (e.name.lower().split() or [""])[0]
            blocks[tok].append(e)
        pairs = []
        for group in blocks.values():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pairs.append((group[i], group[j]))
        pairs = pairs[:threshold_pairs]
        if not pairs:
            return 0

        schema = {"type": "object", "properties": {
            "same": {"type": "array", "items": {
                "type": "object", "properties": {
                    "a": {"type": "integer"}, "b": {"type": "integer"},
                    "same_entity": {"type": "boolean"}},
                "required": ["a", "b", "same_entity"],
                "additionalProperties": False}}},
            "required": ["same"], "additionalProperties": False}
        listing = "\n".join(
            f"{a.id}|{b.id}: A={a.name!r} {a.attrs}  B={b.name!r} {b.attrs}"
            for a, b in pairs)
        data = claude.extract(
            prompt=f"For each pair, decide if A and B are the same real-world "
                   f"{etype}. Be conservative — different locations or clearly "
                   f"different names are NOT the same.\n\n{listing}",
            schema=schema,
            system="You resolve duplicate entities in an OSINT graph.",
            model=claude.fast_model)
        merged = 0
        if data:
            for pair in data.get("same", []):
                if pair.get("same_entity"):
                    self._merge(pair["a"], pair["b"])
                    merged += 1
        return merged

    def _merge(self, keep_id: int, drop_id: int) -> None:
        keep, drop = self.get(keep_id), self.get(drop_id)
        if not (keep and drop):
            return
        with self._lock:
            self._conn.execute("UPDATE entities SET attrs=? WHERE id=?",
                               (json.dumps({**drop.attrs, **keep.attrs}), keep_id))
            self._conn.execute("UPDATE OR IGNORE edges SET src=? WHERE src=?",
                               (keep_id, drop_id))
            self._conn.execute("UPDATE OR IGNORE edges SET dst=? WHERE dst=?",
                               (keep_id, drop_id))
            self._conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (drop_id, drop_id))
            self._conn.execute("DELETE FROM entities WHERE id=?", (drop_id,))
            self._conn.commit()

    # -- export ---------------------------------------------------------------

    def to_html(self, path: str | Path) -> Path:
        """Self-contained interactive link chart (vis-network from CDN)."""
        path = Path(path)
        colors = {"company": "#2F6DB3", "person": "#E07A5F", "domain": "#81B29A",
                  "location": "#F2CC8F", "filing": "#9D8189", "product": "#6D6875"}
        nodes = [{"id": e.id, "label": e.name, "group": e.type,
                  "color": colors.get(e.type, "#999"),
                  "title": f"{e.type}: {e.name}<br>{e.attrs}"}
                 for e in (self.get(r[0]) for r in
                           self._conn.execute("SELECT id FROM entities"))]
        edges = [{"from": s, "to": d, "label": r}
                 for s, d, r in self._conn.execute("SELECT src, dst, rel FROM edges")]
        html = _HTML_TEMPLATE.replace("__NODES__", json.dumps(nodes)) \
                             .replace("__EDGES__", json.dumps(edges)) \
                             .replace("__TITLE__", self.name)
        path.write_text(html)
        return path


_HTML_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<title>__TITLE__ — entity graph</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>body{margin:0;font-family:sans-serif}#g{width:100vw;height:100vh}</style></head>
<body><div id="g"></div><script>
const nodes=new vis.DataSet(__NODES__), edges=new vis.DataSet(__EDGES__);
new vis.Network(document.getElementById('g'),{nodes,edges},{
 nodes:{shape:'dot',size:14,font:{size:13}},
 edges:{arrows:'to',font:{size:10,color:'#777'},color:{color:'#ccc'}},
 physics:{stabilization:true,barnesHut:{springLength:140}}});
</script></body></html>"""
