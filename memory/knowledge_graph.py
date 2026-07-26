"""Personal knowledge graph — links every entity ARIA interacts with."""
from __future__ import annotations
import json, os, threading
from datetime import datetime
from typing import Any

GRAPH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "knowledge_graph.json")
_lock = threading.Lock()
_graph: dict = {"nodes": {}, "edges": []}

def _load() -> None:
    global _graph
    if os.path.exists(GRAPH_PATH):
        try:
            with open(GRAPH_PATH, "r") as f:
                _graph = json.load(f)
        except Exception:
            _graph = {"nodes": {}, "edges": []}

def _save() -> None:
    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    with _lock:
        with open(GRAPH_PATH, "w") as f:
            json.dump(_graph, f, indent=2)

_load()

def add_node(node_id: str, **attrs) -> None:
    with _lock:
        attrs.setdefault("created_at", datetime.now().isoformat())
        _graph["nodes"][node_id] = attrs
    _save()

def add_edge(source: str, target: str, relation: str) -> None:
    with _lock:
        if source not in _graph["nodes"]:
            _graph["nodes"][source] = {"created_at": datetime.now().isoformat()}
        if target not in _graph["nodes"]:
            _graph["nodes"][target] = {"created_at": datetime.now().isoformat()}
        _graph["edges"].append({"source": source, "target": target, "relation": relation, "at": datetime.now().isoformat()})
    _save()

def record_intent(intent: str, parameters: dict, session_id: str = "") -> None:
    now_label = datetime.now().strftime("%Y%m%d_%H%M%S")
    action_id = f"action_{now_label}"
    add_node(action_id, type="action", intent=intent, session=session_id)
    if intent == "open_app":
        app = parameters.get("app_name", "")
        if app:
            add_node(app, type="app")
            add_edge(action_id, app, "opened")
    elif intent == "open_file":
        path = parameters.get("path", "")
        if path:
            add_node(path, type="file")
            add_edge(action_id, path, "opened")
    elif intent == "search_web":
        q = parameters.get("query", "")
        if q:
            add_node(q, type="search")
            add_edge(action_id, q, "searched")
    elif intent == "create_schedule":
        text = parameters.get("text", "")
        if text:
            add_node(text[:40], type="schedule")
            add_edge(action_id, text[:40], "scheduled")

def query(topic: str, hops: int = 2) -> list[dict]:
    with _lock:
        matches = [n for n in _graph["nodes"] if topic.lower() in str(n).lower()]
        connected = set(matches)
        for m in matches:
            for edge in _graph["edges"]:
                if edge["source"] == m:
                    connected.add(edge["target"])
                if edge["target"] == m:
                    connected.add(edge["source"])
        result = []
        for n in connected:
            result.append({"id": n, **_graph["nodes"].get(n, {})})
        return result[:20]

def query_formatted(topic: str) -> str:
    nodes = query(topic)
    if not nodes:
        return f"Sir, I have no knowledge graph entries related to '{topic}' yet."
    lines = [f"🕸️  Knowledge graph — everything related to '{topic}':"]
    for node in nodes:
        ntype = node.get("type", "?")
        lines.append(f"  [{ntype}]  {node['id']}")
    return "\n".join(lines)
