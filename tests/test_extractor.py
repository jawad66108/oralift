import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.extractor import extract_units, extract_from_file, Param


FIXTURE = Path(__file__).parent.parent / "samples" / "legacy_hr_package.sql"


def test_fixture_extracts_all_six_units():
    units = extract_from_file(FIXTURE)
    names = {u.name for u in units}
    assert names == {
        "HIRE_EMPLOYEE",
        "TERMINATE_EMPLOYEE",
        "GET_DEPT_HEADCOUNT",
        "ADJUST_SALARY",
        "RUN_DYNAMIC_REPORT",
        "LOG_AUDIT_EVENT",
    }


def test_each_unit_has_correct_package():
    units = extract_from_file(FIXTURE)
    assert all(u.package == "HR_PKG" for u in units)


def test_hire_employee_params_and_directions():
    units = {u.name: u for u in extract_from_file(FIXTURE)}
    params = units["HIRE_EMPLOYEE"].params
    assert [p.name for p in params] == [
        "p_first_name", "p_last_name", "p_dept_id", "p_salary", "p_emp_id"
    ]
    assert params[-1].direction == "OUT"
    assert params[0].direction == "IN"


def test_function_return_type_captured():
    units = {u.name: u for u in extract_from_file(FIXTURE)}
    assert units["GET_DEPT_HEADCOUNT"].return_type == "NUMBER"
    assert units["HIRE_EMPLOYEE"].return_type is None


def test_call_graph_excludes_false_positives():
    """Regression test: table names in INSERT INTO and datatype size
    specifiers like VARCHAR2(4000) must not appear as calls."""
    units = {u.name: u for u in extract_from_file(FIXTURE)}
    all_calls = set()
    for u in units.values():
        all_calls.update(u.calls)
    assert "EMPLOYEES" not in all_calls
    assert "AUDIT_LOG" not in all_calls
    assert "VARCHAR2" not in all_calls


def test_call_graph_finds_real_calls():
    units = {u.name: u for u in extract_from_file(FIXTURE)}
    assert "LOG_AUDIT_EVENT" in units["HIRE_EMPLOYEE"].calls
    assert "LOG_AUDIT_EVENT" in units["TERMINATE_EMPLOYEE"].calls
    assert "GET_DEPT_HEADCOUNT" in units["ADJUST_SALARY"].calls


def test_unit_bodies_do_not_bleed_into_next_unit():
    """Regression test for the depth-tracking off-by-one: each unit's
    body must end at its OWN `END <name>;`, not run to the package end."""
    units = {u.name: u for u in extract_from_file(FIXTURE)}
    hire = units["HIRE_EMPLOYEE"]
    assert "TERMINATE_EMPLOYEE" not in hire.body
    assert "END HIRE_EMPLOYEE" in hire.body


def test_bare_procedure_without_package_wrapper():
    sql = """
    CREATE OR REPLACE PROCEDURE STANDALONE_PROC(p_x IN NUMBER) IS
    BEGIN
      NULL;
    END STANDALONE_PROC;
    /
    """
    units = extract_units(sql)
    assert len(units) == 1
    assert units[0].name == "STANDALONE_PROC"
    assert units[0].package is None


def test_nested_if_and_loop_do_not_confuse_end_matching():
    sql = """
    CREATE OR REPLACE PROCEDURE NESTED_LOGIC(p_x IN NUMBER) IS
    BEGIN
      IF p_x > 0 THEN
        FOR i IN 1..10 LOOP
          NULL;
        END LOOP;
      END IF;
    END NESTED_LOGIC;

    PROCEDURE AFTER_IT(p_y IN NUMBER) IS
    BEGIN
      NULL;
    END AFTER_IT;
    """
    units = {u.name: u for u in extract_units(sql)}
    assert "AFTER_IT" not in units["NESTED_LOGIC"].body
    assert len(units) == 2


def test_default_param_value_stripped_from_datatype():
    sql = """
    CREATE OR REPLACE PROCEDURE WITH_DEFAULT(p_x IN NUMBER DEFAULT 5) IS
    BEGIN
      NULL;
    END WITH_DEFAULT;
    """
    units = extract_units(sql)
    assert units[0].params[0].datatype.strip() == "NUMBER"
