# OraLift

A legacy PL/SQL modernization assistant. Point it at Oracle PL/SQL
packages and get: a call-dependency graph, a plain-English spec per
procedure/function, a generated REST scaffold with test stubs, and a
risk report naming exactly what it can't safely auto-migrate (dynamic
SQL, autonomous transactions, package-level state, DBMS_* calls).

Built for the **IBM Bob 2.0 Hackathon** (lablab.ai, 25–27 Sep 2026).

## Why this exists

Legacy modernization is IBM Bob's stated strength -- this project
leans into that instead of building a generic code-review bot. The
risk report is deliberately honest about what can't be mechanically
migrated; that's meant to read as engineering maturity, not a gap.

## Status

All five pipeline stages are implemented and tested (43 passing tests
across `tests/`). The app runs end to end: upload a `.sql` file (or
use the bundled fixture) and get extraction → graph → specs → risk
report → downloadable REST scaffold, all in one Streamlit session.

| Stage      | Module              | Status |
|------------|----------------------|--------|
| Extraction | `core/extractor.py`  | ✅ |
| Graph      | `core/grapher.py`    | ✅ risk-aware Mermaid rendering |
| Risk scan  | `core/riskscan.py`   | ✅ 4 rules, severity-rated |
| Specs      | `core/specgen.py`    | ✅ needs `ANTHROPIC_API_KEY` |
| Scaffold   | `core/scaffold.py`   | ✅ FastAPI routes + pytest, zipped |

Run the whole suite: `python3 -m pytest tests/ -v`

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app defaults to the bundled fixture (`samples/legacy_hr_package.sql`)
if no file is uploaded, so it's demoable with zero setup. Spec
generation needs an Anthropic API key -- either export
`ANTHROPIC_API_KEY` before launching, or paste it into the sidebar
field in the running app (not persisted anywhere, just set into the
process environment for that session).

## Architecture

```
uploaded .sql files
        |
        v
  extractor.py --> [ {name, type, params, body, calls[]} ]
        |
        +--> grapher.py --> networkx --> Mermaid diagram
        |
        +--> specgen.py --> LLM --> plain-English spec per unit
        |
        +--> scaffold.py --> Jinja2 --> REST routes + pytest stubs (.zip)
        |
        +--> riskscan.py --> flags: dynamic SQL, autonomous txns,
                              package state, DBMS_* dependencies
```

`riskscan.py` is a hardcoded rules pass, not an LLM call -- fast,
deterministic, and easy to defend live in the demo.

## Extractor design notes

`core/extractor.py` is regex-based on purpose -- a real PL/SQL grammar
is not worth building in a 48-hour window. It handles:

- `CREATE OR REPLACE PACKAGE BODY` blocks with multiple procedures/functions
- Parameter lists with `IN` / `OUT` / `IN OUT` and default values
- Nested `BEGIN...END`, `IF...END IF`, `LOOP...END LOOP`, `CASE...END`
  via depth tracking, so each unit's body boundary is found correctly
- Call-site detection with false-positive suppression for:
  - Oracle datatypes with size/precision (`VARCHAR2(4000)`)
  - Table names in `INSERT INTO table (...)`

Known limitations (see extractor.py's module docstring for the full
list): no nested-procedure support, overloaded units collide by name,
string literals aren't specially escaped (which is actually *useful*
for riskscan's dynamic-SQL detection, since that's exactly where
dynamic SQL text lives).

Run it standalone against the fixture:

```bash
python3 core/extractor.py samples/legacy_hr_package.sql
```

## Demo fixture

`samples/legacy_hr_package.sql` is a realistic 6-procedure HR package
with, by design:
- a **dynamic SQL landmine** (`RUN_DYNAMIC_REPORT`, string-concatenated
  `OPEN ... FOR`, SQL-injection-shaped)
- an **autonomous transaction** (`LOG_AUDIT_EVENT`,
  `PRAGMA AUTONOMOUS_TRANSACTION`)
- **package-level state** (`g_last_audit_ts`, `g_cache_dept_count`)

These three are exactly what the risk report is supposed to catch --
use this fixture to sanity-check `riskscan.py` as it's built.

## Using IBM Bob during the build

Per the submission requirements, narrate Bob usage in the demo video:
Architect Mode for planning each module's interface (the docstring
sketches in the stubbed `core/*.py` files were written with exactly
this handoff in mind), Code Mode for implementation. Screenshot both.

## Hackathon submission checklist

- [ ] Confirm the specific challenge brief once published (theme was
      still "TBA" as of the project brief date -- check the live event
      page before finalizing scope)
- [ ] Register before kickoff (Fri 25 Sep, 8:00 PM PKT) -- required to
      get Bob access at all
- [ ] Project title, short + long description, tech/category tags
- [ ] 16:9 cover image (PNG/JPG)
- [ ] Demo video (MP4, max 5 min) -- narrate Bob usage in both modes
- [ ] Pitch deck (PDF)
- [ ] Public GitHub repo
- [ ] Live app URL (Streamlit/Replit/Vercel)
- [ ] Create a team on the platform (required even solo)
- [ ] Submit before deadline: Sun 27 Sep, 8:00 PM PKT
