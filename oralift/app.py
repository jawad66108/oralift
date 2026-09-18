"""
OraLift -- Legacy PL/SQL Modernization Assistant
Streamlit entry point.

Pipeline (per ARCHITECTURE.md in the project brief):
  uploaded .sql files
        -> extractor.py   -> [Unit] (name, type, params, body, calls[])
        -> grapher.py     -> networkx -> Mermaid diagram
        -> specgen.py     -> LLM -> plain-English spec per unit
        -> scaffold.py    -> Jinja2 -> REST routes + pytest stubs (.zip)
        -> riskscan.py    -> flags: dynamic SQL, autonomous txns,
                             package state, DBMS_* dependencies

Implemented and tested: extractor.py, riskscan.py, grapher.py,
specgen.py, scaffold.py. The full pipeline is wired end to end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from core.extractor import extract_units, Unit
from core.riskscan import scan_risks, sort_findings
from core.grapher import build_graph, to_mermaid, to_mermaid_html, find_cycles
from core.specgen import generate_specs_cached
from core.scaffold import generate_scaffold_zip


st.set_page_config(page_title="OraLift", page_icon="🛠️", layout="wide")

st.title("🛠️ OraLift")
st.caption("Point it at legacy Oracle PL/SQL. Get a dependency map, plain-English specs, "
           "a REST scaffold, and an honest risk report on what it can't safely auto-migrate.")

# ---------------------------------------------------------------------
# Input: uploaded .sql files, or fall back to the bundled sample.
# ---------------------------------------------------------------------
with st.sidebar:
    st.header("Input")
    uploaded = st.file_uploader(
        "Upload PL/SQL file(s) (.sql)", type=["sql"], accept_multiple_files=True
    )
    use_sample = st.checkbox("Use bundled sample (samples/legacy_hr_package.sql)", value=not uploaded)
    st.divider()
    st.header("Spec generation")
    api_key_input = st.text_input(
        "Anthropic API key", type="password",
        value=os.environ.get("ANTHROPIC_API_KEY", ""),
        help="Only used for the Specs tab. Leave blank and set ANTHROPIC_API_KEY "
             "as an environment variable instead if you don't want to paste it here.",
    )
    if api_key_input:
        os.environ["ANTHROPIC_API_KEY"] = api_key_input
    st.divider()
    st.caption("Built with IBM Bob 2.0 -- Architect Mode for planning, Code Mode for implementation.")


def _load_sources() -> dict[str, str]:
    """Returns {filename: sql_text} for whatever the user gave us."""
    sources: dict[str, str] = {}
    if uploaded:
        for f in uploaded:
            sources[f.name] = f.read().decode("utf-8", errors="replace")
    if use_sample or not sources:
        sample_path = Path(__file__).parent / "samples" / "legacy_hr_package.sql"
        if sample_path.exists():
            sources[sample_path.name] = sample_path.read_text(encoding="utf-8")
    return sources


sources = _load_sources()

if not sources:
    st.info("Upload one or more .sql files, or check 'Use bundled sample' in the sidebar.")
    st.stop()

# ---------------------------------------------------------------------
# Stage 1: Extraction (real)
# ---------------------------------------------------------------------
all_units: list[Unit] = []
for fname, text in sources.items():
    all_units.extend(extract_units(text, source_file=fname))

if not all_units:
    st.warning("No PROCEDURE or FUNCTION units were found in the supplied file(s). "
               "Check that the file contains a CREATE OR REPLACE PACKAGE BODY with "
               "standard IS/BEGIN/END structure.")
    st.stop()

# ---------------------------------------------------------------------
# Stage 2: Risk scan (real) -- computed once here so both the graph
# tab (for risk-aware node styling) and the risk tab can use it.
# Run per source file so PACKAGE_STATE can see each file's own
# package-spec variables (extract_package_variables only looks at
# one PACKAGE block per call).
# ---------------------------------------------------------------------
all_findings = []
for fname, text in sources.items():
    file_units = [u for u in all_units if u.source_file == fname]
    all_findings.extend(scan_risks(file_units, source_text=text))
all_findings = sort_findings(all_findings)
risky_unit_names = {f.unit_name for f in all_findings}

tab_overview, tab_graph, tab_specs, tab_scaffold, tab_risk = st.tabs(
    ["📋 Overview", "🕸️ Dependency Graph", "📝 Specs", "⚙️ REST Scaffold", "⚠️ Risk Report"]
)

# ---------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------
with tab_overview:
    st.subheader(f"Extracted {len(all_units)} unit(s) from {len(sources)} file(s)")
    rows = [
        {
            "Name": u.name,
            "Type": u.unit_type,
            "Package": u.package or "-",
            "Params": len(u.params),
            "Calls": ", ".join(u.calls) if u.calls else "-",
            "Lines": f"{u.start_line}-{u.end_line}",
        }
        for u in all_units
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("Raw extracted JSON (for debugging)"):
        st.json([u.to_dict() for u in all_units])

# ---------------------------------------------------------------------
# Dependency graph tab (real -- core/grapher.py)
# ---------------------------------------------------------------------
with tab_graph:
    st.subheader("Call dependency graph")
    st.caption("Nodes flagged red have at least one riskscan finding -- "
               "this graph and the Risk Report tab agree by construction.")

    graph = build_graph(all_units)
    cycles = find_cycles(graph)
    if cycles:
        st.warning(f"⚠️ Mutual recursion detected: {', '.join(' ↔ '.join(c) for c in cycles)}. "
                   "These units can't be migrated one at a time without breaking the cycle.")

    mermaid_src = to_mermaid(graph, risk_units=risky_unit_names)
    components.html(to_mermaid_html(mermaid_src, height=400), height=420, scrolling=True)

    with st.expander("Mermaid source"):
        st.code(mermaid_src, language="mermaid")

# ---------------------------------------------------------------------
# Specs tab (real -- core/specgen.py, one LLM call per unit, cached
# in session_state so re-running on the same file doesn't re-spend
# API calls)
# ---------------------------------------------------------------------
with tab_specs:
    st.subheader("Plain-English specs")
    st.caption("One LLM call per unit, using prompts/spec.md. Cached in this session "
               "so re-selecting a unit you've already generated doesn't re-spend a call.")

    if "spec_cache" not in st.session_state:
        st.session_state["spec_cache"] = {}

    selected = st.selectbox("Choose a unit", [u.name for u in all_units])
    unit = next(u for u in all_units if u.name == selected)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown(f"**{unit.unit_type} {unit.name}**")
        st.code(unit.body[:2000], language="sql")

    with col2:
        cache_key = (unit.name, hash(unit.body))
        cache = st.session_state["spec_cache"]

        if cache_key in cache:
            spec_text = cache[cache_key]
        elif st.button(f"Generate spec for {unit.name}"):
            with st.spinner("Calling the LLM..."):
                results = generate_specs_cached([unit], cache)
                spec_text = results[unit.name]
        else:
            spec_text = None

        if spec_text:
            if spec_text.startswith("ERROR:"):
                st.error(spec_text)
            else:
                st.markdown(spec_text)

# ---------------------------------------------------------------------
# Scaffold tab (real -- core/scaffold.py)
# ---------------------------------------------------------------------
with tab_scaffold:
    st.subheader("REST scaffold")
    st.caption("One FastAPI route + one pytest stub per unit, rendered from "
               "templates/route.py.j2 and templates/test_route.py.j2. Units with a "
               "riskscan finding still get a scaffold, but with a prominent warning "
               "docstring instead of being silently skipped.")

    st.markdown(f"Will generate **{len(all_units)} route(s)** and **{len(all_units)} test file(s)** "
                f"({len(risky_unit_names)} flagged as risky).")

    zip_bytes = generate_scaffold_zip(all_units, risky_unit_names=risky_unit_names)
    st.download_button(
        "⬇️ Download scaffold (.zip)",
        data=zip_bytes,
        file_name="oralift_scaffold.zip",
        mime="application/zip",
    )

    preview_unit = st.selectbox("Preview a generated route", [u.name for u in all_units], key="scaffold_preview")
    unit = next(u for u in all_units if u.name == preview_unit)
    from core.scaffold import render_route
    st.code(render_route(unit, is_risky=unit.name in risky_unit_names), language="python")

# ---------------------------------------------------------------------
# Risk report tab (real -- core/riskscan.py; all_findings computed above)
# ---------------------------------------------------------------------
with tab_risk:
    st.subheader("Risk report")
    st.caption("Hardcoded rules pass over the extracted units -- not an LLM call. "
                "Every finding below traces to an exact rule, not a model's opinion.")

    if not all_findings:
        st.success("No risk patterns detected by the current rule set.")
    else:
        severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in all_findings:
            severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

        cols = st.columns(3)
        cols[0].metric("🔴 High", severity_counts["HIGH"])
        cols[1].metric("🟡 Medium", severity_counts["MEDIUM"])
        cols[2].metric("🟢 Low", severity_counts["LOW"])

        st.divider()

        icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
        for f in all_findings:
            with st.expander(f"{icon[f.severity]} **{f.unit_name}** -- {f.rule} ({f.severity})"):
                st.markdown(f.message)
                st.code(f.evidence, language="sql")

        with st.expander("Raw findings JSON (for debugging)"):
            st.json([f.to_dict() for f in all_findings])
