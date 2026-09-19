import ast
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.extractor import extract_from_file
from core.scaffold import generate_scaffold_zip, py_type, render_route, render_test


FIXTURE = Path(__file__).parent.parent / "samples" / "legacy_hr_package.sql"


def _get_unit(name: str):
    units = {u.name: u for u in extract_from_file(FIXTURE)}
    return units[name]


def test_py_type_strips_size_and_maps_known_types():
    assert py_type("VARCHAR2(4000)") == "str"
    assert py_type("NUMBER") == "float"
    assert py_type("DATE") == "str"


def test_py_type_falls_back_to_str_for_unknown():
    assert py_type("SOME_CUSTOM_TYPE") == "str"
    assert py_type(None) == "str"
    assert py_type("UNKNOWN") == "str"


def test_render_route_produces_valid_python():
    unit = _get_unit("HIRE_EMPLOYEE")
    code = render_route(unit, is_risky=False)
    ast.parse(code)  # raises SyntaxError if invalid


def test_render_route_risky_flag_adds_warning_text():
    unit = _get_unit("RUN_DYNAMIC_REPORT")
    risky_code = render_route(unit, is_risky=True)
    safe_code = render_route(unit, is_risky=False)
    assert "flagged by riskscan.py" in risky_code
    assert "flagged by riskscan.py" not in safe_code


def test_render_route_in_params_become_request_fields():
    unit = _get_unit("HIRE_EMPLOYEE")
    code = render_route(unit, is_risky=False)
    assert "first_name: str" in code
    assert "dept_id: float" in code


def test_render_route_out_params_become_response_fields():
    unit = _get_unit("HIRE_EMPLOYEE")
    code = render_route(unit, is_risky=False)
    assert "emp_id: float" in code
    assert "class HireemployeeResponse" in code


def test_render_route_function_return_type_becomes_result_field():
    unit = _get_unit("GET_DEPT_HEADCOUNT")
    code = render_route(unit, is_risky=False)
    assert "result: float" in code


def test_render_test_produces_valid_python():
    unit = _get_unit("HIRE_EMPLOYEE")
    code = render_test(unit)
    ast.parse(code)


def test_generate_scaffold_zip_contains_one_route_and_test_per_unit():
    units = extract_from_file(FIXTURE)
    zip_bytes = generate_scaffold_zip(units)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
    for unit in units:
        assert f"routes/{unit.name.lower()}.py" in names
        assert f"tests/test_{unit.name.lower()}.py" in names
    assert "README.md" in names


def test_generate_scaffold_zip_all_files_are_valid_python():
    units = extract_from_file(FIXTURE)
    zip_bytes = generate_scaffold_zip(units, risky_unit_names={"RUN_DYNAMIC_REPORT"})
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith(".py"):
                ast.parse(zf.read(name).decode("utf-8"))


def test_generate_scaffold_zip_readme_flags_risky_units():
    units = extract_from_file(FIXTURE)
    zip_bytes = generate_scaffold_zip(units, risky_unit_names={"RUN_DYNAMIC_REPORT", "LOG_AUDIT_EVENT"})
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        readme = zf.read("README.md").decode("utf-8")
    assert "RUN_DYNAMIC_REPORT" in readme and "RISKY" in readme.split("RUN_DYNAMIC_REPORT")[1].split("\n")[0]
    assert "HIRE_EMPLOYEE" in readme
    assert "RISKY" not in readme.split("HIRE_EMPLOYEE")[1].split("\n")[0]
