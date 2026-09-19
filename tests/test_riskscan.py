import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.extractor import extract_units, extract_from_file, extract_package_variables
from core.riskscan import scan_risks


FIXTURE = Path(__file__).parent.parent / "samples" / "legacy_hr_package.sql"


def _findings_for_fixture():
    units = extract_from_file(FIXTURE)
    source = FIXTURE.read_text(encoding="utf-8")
    return scan_risks(units, source_text=source)


def test_fixture_flags_dynamic_sql_in_run_dynamic_report():
    findings = _findings_for_fixture()
    hits = [f for f in findings if f.unit_name == "RUN_DYNAMIC_REPORT" and f.rule == "DYNAMIC_SQL"]
    assert len(hits) == 1
    assert hits[0].severity == "HIGH"


def test_fixture_flags_autonomous_txn_in_log_audit_event():
    findings = _findings_for_fixture()
    hits = [f for f in findings if f.unit_name == "LOG_AUDIT_EVENT" and f.rule == "AUTONOMOUS_TXN"]
    assert len(hits) == 1
    assert hits[0].severity == "MEDIUM"


def test_fixture_flags_package_state_writes():
    findings = _findings_for_fixture()
    package_state_units = {f.unit_name for f in findings if f.rule == "PACKAGE_STATE"}
    assert package_state_units == {"GET_DEPT_HEADCOUNT", "LOG_AUDIT_EVENT"}


def test_fixture_has_no_false_positive_dynamic_sql_on_static_units():
    findings = _findings_for_fixture()
    dyn_units = {f.unit_name for f in findings if f.rule == "DYNAMIC_SQL"}
    # HIRE_EMPLOYEE etc only do static SQL -- must not be flagged.
    assert "HIRE_EMPLOYEE" not in dyn_units
    assert "TERMINATE_EMPLOYEE" not in dyn_units
    assert "ADJUST_SALARY" not in dyn_units


def test_open_cursor_for_select_is_not_flagged():
    """A static `OPEN cur FOR SELECT ...` must NOT be treated as dynamic SQL."""
    sql = """
    CREATE OR REPLACE PROCEDURE STATIC_CURSOR IS
      v_cur SYS_REFCURSOR;
    BEGIN
      OPEN v_cur FOR SELECT * FROM employees;
      CLOSE v_cur;
    END STATIC_CURSOR;
    """
    units = extract_units(sql)
    findings = scan_risks(units)
    assert not any(f.rule == "DYNAMIC_SQL" for f in findings)


def test_execute_immediate_is_flagged_high():
    sql = """
    CREATE OR REPLACE PROCEDURE RUNS_DDL(p_tbl IN VARCHAR2) IS
    BEGIN
      EXECUTE IMMEDIATE 'DROP TABLE ' || p_tbl;
    END RUNS_DDL;
    """
    units = extract_units(sql)
    findings = scan_risks(units)
    assert any(f.rule == "DYNAMIC_SQL" and f.severity == "HIGH" for f in findings)


def test_dbms_output_is_low_severity_dbms_lock_is_high():
    sql = """
    CREATE OR REPLACE PROCEDURE USES_DBMS IS
    BEGIN
      DBMS_OUTPUT.PUT_LINE('hello');
      DBMS_LOCK.SLEEP(1);
    END USES_DBMS;
    """
    units = extract_units(sql)
    findings = {f.evidence: f.severity for f in scan_risks(units) if f.rule == "DBMS_CALL"}
    assert findings["DBMS_OUTPUT"] == "LOW"
    assert findings["DBMS_LOCK"] == "HIGH"


def test_no_source_text_skips_package_state_gracefully():
    units = extract_from_file(FIXTURE)
    findings = scan_risks(units, source_text=None)
    assert not any(f.rule == "PACKAGE_STATE" for f in findings)
    # other rules should still run
    assert any(f.rule == "DYNAMIC_SQL" for f in findings)
