"""
scaffold.py
-----------
Renders templates/route.py.j2 and templates/test_route.py.j2 once per
unit (one FastAPI route + one pytest stub each), and zips the result
in-memory for Streamlit's st.download_button.

Mapping decisions (see templates/*.j2 for where these land):
  - IN params      -> request body field
  - OUT params     -> response field
  - IN OUT params  -> both a request field AND a response field
                      (flagged in the route's docstring -- this is
                      semantically awkward over HTTP and worth calling
                      out rather than hiding)
  - A function's RETURN type -> a `result` field on the response
  - Units with any riskscan finding still get a scaffold, but the
    generated route carries a prominent warning docstring instead of
    being silently skipped -- the tool's whole point is surfacing
    risk, not hiding it by omission.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from core.extractor import Unit

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Oracle PL/SQL datatype -> Python/Pydantic type. Not exhaustive --
# extend as real customer schemas surface types not covered here.
# Unknown types fall back to `str` rather than crashing generation,
# since a wrong-but-valid stub is more useful mid-hackathon than a
# generation failure.
_PLSQL_TO_PY_TYPE = {
    "VARCHAR2": "str", "NVARCHAR2": "str", "CHAR": "str", "NCHAR": "str",
    "CLOB": "str", "LONG": "str",
    "NUMBER": "float", "INTEGER": "int", "PLS_INTEGER": "int",
    "BINARY_INTEGER": "int", "FLOAT": "float",
    "DATE": "str", "TIMESTAMP": "str",  # ISO string over the wire
    "BOOLEAN": "bool",
    "RAW": "bytes", "BLOB": "bytes",
}


def py_type(datatype: str | None) -> str:
    """Jinja filter: map a PL/SQL datatype string to a Python type
    annotation. Strips size/precision (e.g. "VARCHAR2(50)" -> "VARCHAR2")
    before lookup."""
    if not datatype or datatype == "UNKNOWN":
        return "str"
    base = datatype.strip().split("(")[0].strip().upper()
    return _PLSQL_TO_PY_TYPE.get(base, "str")


def _get_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["py_type"] = py_type
    return env


def render_route(unit: Unit, is_risky: bool, env: Environment | None = None) -> str:
    env = env or _get_env()
    template = env.get_template("route.py.j2")
    request_params = [p for p in unit.params if p.direction in ("IN", "IN OUT")]
    response_params = [p for p in unit.params if p.direction in ("OUT", "IN OUT")]
    return template.render(
        unit=unit,
        is_risky=is_risky,
        request_params=request_params,
        response_params=response_params,
    )


def render_test(unit: Unit, env: Environment | None = None) -> str:
    env = env or _get_env()
    template = env.get_template("test_route.py.j2")
    request_params = [p for p in unit.params if p.direction in ("IN", "IN OUT")]
    return template.render(unit=unit, request_params=request_params)


def generate_scaffold_zip(units: list[Unit], risky_unit_names: set[str] | None = None) -> bytes:
    """Render a route + test file per unit and return zip bytes.

    Layout inside the zip:
        routes/<unit_name_lower>.py
        tests/test_<unit_name_lower>.py
        README.md  -- one line per unit noting risky ones
    """
    risky_unit_names = risky_unit_names or set()
    env = _get_env()

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        readme_lines = ["# OraLift-generated REST scaffold", ""]
        for unit in units:
            is_risky = unit.name in risky_unit_names
            route_code = render_route(unit, is_risky=is_risky, env=env)
            test_code = render_test(unit, env=env)

            zf.writestr(f"routes/{unit.name.lower()}.py", route_code)
            zf.writestr(f"tests/test_{unit.name.lower()}.py", test_code)

            flag = " ⚠️ RISKY -- see risk report before trusting this scaffold" if is_risky else ""
            readme_lines.append(f"- `{unit.name}` ({unit.unit_type}){flag}")

        zf.writestr("README.md", "\n".join(readme_lines))

    return buffer.getvalue()


if __name__ == "__main__":
    import sys
    from core.extractor import extract_from_file

    target = sys.argv[1] if len(sys.argv) > 1 else "samples/legacy_hr_package.sql"
    units = extract_from_file(target)
    zip_bytes = generate_scaffold_zip(units, risky_unit_names={"RUN_DYNAMIC_REPORT", "LOG_AUDIT_EVENT"})

    out_path = Path("scaffold_output.zip")
    out_path.write_bytes(zip_bytes)
    print(f"Wrote {out_path} ({len(zip_bytes)} bytes)")
