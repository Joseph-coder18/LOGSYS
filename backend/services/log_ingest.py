import re
import logging
from typing import Any
from services.graph_store import _resolve_graph, _persist

logger = logging.getLogger("logmind.log_ingest")

# Patterns for extracting services, targets, databases, and compute nodes
SERVICE_RE = re.compile(r'(?:service|component|module|app)[=:\s"\']+([a-zA-Z0-9_-]+)', re.IGNORECASE)
TARGET_RE = re.compile(r'target_service[=:\s"\']+([a-zA-Z0-9_-]+)', re.IGNORECASE)
DB_RE = re.compile(r'\b([a-zA-Z0-9_-]+-db|mysql|postgres(?:ql)?|redis|mongodb|sqlite|elasticsearch|cassandra|dynamodb)\b', re.IGNORECASE)
NODE_RE = re.compile(r'\b(node-\d+)\b', re.IGNORECASE)

def ingest_log_content(text: str, session_id: str | None = None) -> None:
    """Parse log text and extract service nodes and dependencies into the knowledge graph."""
    if not text:
        return

    graph, _ = _resolve_graph(session_id)
    lines = text.splitlines()

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        # Extract source service
        service_match = SERVICE_RE.search(line_str)
        if not service_match:
            continue
        service = service_match.group(1).strip()
        if service:
            graph.add_node(service, type="service", service=service)

        # Extract target service calls
        for target in TARGET_RE.findall(line_str):
            target = target.strip()
            if target and target != service:
                graph.add_node(target, type="service", service=target)
                graph.add_edge(service, target, rel="depends_on")

        # Extract database interactions
        for db in DB_RE.findall(line_str):
            db = db.strip()
            if db and db != service:
                graph.add_node(db, type="database", service=db)
                graph.add_edge(service, db, rel="depends_on")

        # Extract compute node interactions
        for node in NODE_RE.findall(line_str):
            node = node.strip()
            if node and node != service:
                graph.add_node(node, type="service", service=node)
                graph.add_edge(service, node, rel="depends_on")

    _persist(graph, session_id)
    logger.info("Ingested log graph for session %s: %d nodes, %d edges", session_id, graph.number_of_nodes(), graph.number_of_edges())

def ingest_log_line(line: str, session_id: str | None = None) -> None:
    """Ingest a single line (backward compatibility)."""
    ingest_log_content(line, session_id=session_id)
