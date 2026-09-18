"""
grapher.py
----------
Builds a call-dependency graph from extracted units and renders it as
Mermaid syntax for display in Streamlit.

Design choices:
  - Only edges between units WE extracted are kept. A call to
    something outside the supplied files (another package not
    uploaded, a DB built-in already filtered by extractor's keyword
    list) has no node to point to, so it's dropped here rather than
    creating dangling nodes -- riskscan.py is the right place to
    surface "calls something we can't see" as its own concern, not
    this module.
  - Cycle detection is included because legacy PL/SQL packages
    routinely have mutual recursion between procedures within the
    same package; that's worth flagging directly rather than letting
    it show up as a visually tangled graph with no explanation.
  - to_mermaid() accepts risk_units so the graph and the Risk Report
    tab agree by construction: a unit riskscan.py flagged is drawn in
    the same red here as it's marked there, rather than maintaining
    two independent ideas of "risky."
"""

from __future__ import annotations

import networkx as nx

from core.extractor import Unit


def build_graph(units: list[Unit]) -> nx.DiGraph:
    """One node per unit (keyed by name), one edge per call where the
    callee is also a known unit."""
    graph = nx.DiGraph()
    known_names = {u.name for u in units}

    for u in units:
        graph.add_node(u.name, unit_type=u.unit_type, package=u.package or "")

    for u in units:
        for callee in u.calls:
            if callee in known_names:
                graph.add_edge(u.name, callee)

    return graph


def find_cycles(graph: nx.DiGraph) -> list[list[str]]:
    """Return simple cycles in the call graph (mutual/indirect
    recursion). Empty list if the graph is a DAG."""
    return list(nx.simple_cycles(graph))


def to_mermaid(graph: nx.DiGraph, risk_units: set[str] | None = None) -> str:
    """Render as Mermaid `graph TD` syntax. Nodes are shaped by unit
    type (rounded = function, rectangle = procedure) so the diagram
    carries that information without a separate legend. Nodes in
    risk_units get a red `risky` class applied.
    """
    risk_units = risk_units or set()

    if graph.number_of_nodes() == 0:
        return "graph TD\n    EMPTY[No units extracted]"

    lines = ["graph TD"]

    for name, data in graph.nodes(data=True):
        unit_type = data.get("unit_type", "PROCEDURE")
        if unit_type == "FUNCTION":
            lines.append(f"    {name}(({name}))")   # rounded = function
        else:
            lines.append(f"    {name}[{name}]")      # rectangle = procedure

    if graph.number_of_edges() == 0:
        lines.append("    %% No internal calls detected between extracted units")
    else:
        for src, dst in graph.edges():
            lines.append(f"    {src} --> {dst}")

    flagged = sorted(n for n in graph.nodes() if n in risk_units)
    if flagged:
        lines.append("    classDef risky fill:#ffcccc,stroke:#cc0000,stroke-width:2px")
        lines.append(f"    class {','.join(flagged)} risky")

    return "\n".join(lines)


def to_mermaid_html(mermaid_src: str, height: int = 400) -> str:
    """Wrap Mermaid source in a minimal self-contained HTML page for
    Streamlit's components.html(), which sandboxes each call in its
    own iframe (no access to the app's own <script> tags, so Mermaid
    has to be loaded fresh here via the mermaid.js UMD build from
    cdnjs rather than assumed to already be on the page).
    """
    return f"""
    <div class="mermaid">
    {mermaid_src}
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.9.1/mermaid.min.js"></script>
    <script>
      mermaid.initialize({{ startOnLoad: true, theme: "default" }});
    </script>
    <style>
      body {{ margin: 0; }}
      .mermaid {{ max-height: {height - 20}px; }}
    </style>
    """


if __name__ == "__main__":
    import sys
    from core.extractor import extract_from_file

    target = sys.argv[1] if len(sys.argv) > 1 else "samples/legacy_hr_package.sql"
    units = extract_from_file(target)
    graph = build_graph(units)
    print(to_mermaid(graph))
    cycles = find_cycles(graph)
    print("\nCycles detected:" if cycles else "\nNo cycles detected.", cycles or "")
