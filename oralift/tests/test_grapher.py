import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.extractor import extract_units, extract_from_file
from core.grapher import build_graph, to_mermaid, find_cycles


FIXTURE = Path(__file__).parent.parent / "samples" / "legacy_hr_package.sql"


def test_fixture_graph_has_six_nodes():
    units = extract_from_file(FIXTURE)
    graph = build_graph(units)
    assert graph.number_of_nodes() == 6


def test_fixture_graph_edges_match_known_calls():
    units = extract_from_file(FIXTURE)
    graph = build_graph(units)
    assert ("HIRE_EMPLOYEE", "LOG_AUDIT_EVENT") in graph.edges
    assert ("ADJUST_SALARY", "GET_DEPT_HEADCOUNT") in graph.edges


def test_fixture_has_no_cycles():
    units = extract_from_file(FIXTURE)
    graph = build_graph(units)
    assert find_cycles(graph) == []


def test_mutual_recursion_detected_as_cycle():
    sql = """
    CREATE OR REPLACE PROCEDURE PING(p_x IN NUMBER) IS
    BEGIN
      PONG(p_x);
    END PING;

    PROCEDURE PONG(p_x IN NUMBER) IS
    BEGIN
      PING(p_x);
    END PONG;
    """
    units = extract_units(sql)
    graph = build_graph(units)
    cycles = find_cycles(graph)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"PING", "PONG"}


def test_calls_to_unknown_units_are_dropped_not_dangling():
    """A call to something not in the extracted set (external package,
    built-in) must not create a phantom node."""
    sql = """
    CREATE OR REPLACE PROCEDURE CALLS_EXTERNAL IS
    BEGIN
      SOME_OTHER_SCHEMA.EXTERNAL_PROC(1);
    END CALLS_EXTERNAL;
    """
    units = extract_units(sql)
    graph = build_graph(units)
    assert graph.number_of_nodes() == 1
    assert graph.number_of_edges() == 0


def test_mermaid_output_includes_all_nodes_and_edges():
    units = extract_from_file(FIXTURE)
    graph = build_graph(units)
    mermaid = to_mermaid(graph)
    assert mermaid.startswith("graph TD")
    for node in graph.nodes:
        assert node in mermaid
    assert "HIRE_EMPLOYEE --> LOG_AUDIT_EVENT" in mermaid


def test_mermaid_risk_styling_only_applied_to_flagged_units():
    units = extract_from_file(FIXTURE)
    graph = build_graph(units)
    mermaid = to_mermaid(graph, risk_units={"RUN_DYNAMIC_REPORT", "LOG_AUDIT_EVENT"})
    assert "classDef risky" in mermaid
    assert "RUN_DYNAMIC_REPORT" in mermaid.split("class ")[-1]


def test_empty_unit_list_does_not_crash():
    graph = build_graph([])
    mermaid = to_mermaid(graph)
    assert "No units extracted" in mermaid
