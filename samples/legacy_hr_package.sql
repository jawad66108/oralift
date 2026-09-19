-- =====================================================================
-- HR_PKG - Legacy Human Resources package
-- Originally written ~2004, patched heavily since. Typical enterprise
-- Oracle package: mixed responsibilities, some risky constructs, no
-- tests, minimal comments. This is the kind of thing OraLift targets.
-- =====================================================================

CREATE OR REPLACE PACKAGE HR_PKG AS

  g_last_audit_ts   DATE;          -- package-level state, set by LOG_AUDIT_EVENT
  g_cache_dept_count NUMBER := 0;  -- package-level state, set by GET_DEPT_HEADCOUNT

  PROCEDURE HIRE_EMPLOYEE(
    p_first_name  IN VARCHAR2,
    p_last_name   IN VARCHAR2,
    p_dept_id     IN NUMBER,
    p_salary      IN NUMBER,
    p_emp_id      OUT NUMBER
  );

  PROCEDURE TERMINATE_EMPLOYEE(
    p_emp_id      IN NUMBER,
    p_reason      IN VARCHAR2
  );

  FUNCTION GET_DEPT_HEADCOUNT(
    p_dept_id     IN NUMBER
  ) RETURN NUMBER;

  PROCEDURE ADJUST_SALARY(
    p_emp_id      IN NUMBER,
    p_pct_change  IN NUMBER
  );

  PROCEDURE RUN_DYNAMIC_REPORT(
    p_table_name  IN VARCHAR2,
    p_filter_col  IN VARCHAR2,
    p_filter_val  IN VARCHAR2
  );

  PROCEDURE LOG_AUDIT_EVENT(
    p_emp_id      IN NUMBER,
    p_event_type  IN VARCHAR2,
    p_details     IN VARCHAR2
  );

END HR_PKG;
/

CREATE OR REPLACE PACKAGE BODY HR_PKG AS

  -- ---------------------------------------------------------------
  -- HIRE_EMPLOYEE
  -- Inserts a new employee row, assigns next EMP_ID from sequence,
  -- and logs the hire via LOG_AUDIT_EVENT (autonomous transaction).
  -- ---------------------------------------------------------------
  PROCEDURE HIRE_EMPLOYEE(
    p_first_name  IN VARCHAR2,
    p_last_name   IN VARCHAR2,
    p_dept_id     IN NUMBER,
    p_salary      IN NUMBER,
    p_emp_id      OUT NUMBER
  ) IS
  BEGIN
    SELECT emp_seq.NEXTVAL INTO p_emp_id FROM dual;

    INSERT INTO employees (emp_id, first_name, last_name, dept_id, salary, hire_date, status)
    VALUES (p_emp_id, p_first_name, p_last_name, p_dept_id, p_salary, SYSDATE, 'ACTIVE');

    LOG_AUDIT_EVENT(p_emp_id, 'HIRE', 'New hire in dept ' || p_dept_id);

    COMMIT;
  EXCEPTION
    WHEN OTHERS THEN
      ROLLBACK;
      RAISE_APPLICATION_ERROR(-20001, 'HIRE_EMPLOYEE failed: ' || SQLERRM);
  END HIRE_EMPLOYEE;

  -- ---------------------------------------------------------------
  -- TERMINATE_EMPLOYEE
  -- Marks employee inactive, records termination reason, audits it.
  -- ---------------------------------------------------------------
  PROCEDURE TERMINATE_EMPLOYEE(
    p_emp_id      IN NUMBER,
    p_reason      IN VARCHAR2
  ) IS
  BEGIN
    UPDATE employees
       SET status = 'TERMINATED',
           term_date = SYSDATE,
           term_reason = p_reason
     WHERE emp_id = p_emp_id;

    IF SQL%ROWCOUNT = 0 THEN
      RAISE_APPLICATION_ERROR(-20002, 'No such employee: ' || p_emp_id);
    END IF;

    LOG_AUDIT_EVENT(p_emp_id, 'TERMINATE', p_reason);

    COMMIT;
  END TERMINATE_EMPLOYEE;

  -- ---------------------------------------------------------------
  -- GET_DEPT_HEADCOUNT
  -- Reads active headcount for a department. Caches last result in
  -- package-level state (g_cache_dept_count) -- stateful, dangerous
  -- to migrate naively since REST calls are stateless.
  -- ---------------------------------------------------------------
  FUNCTION GET_DEPT_HEADCOUNT(
    p_dept_id     IN NUMBER
  ) RETURN NUMBER IS
    v_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO v_count
      FROM employees
     WHERE dept_id = p_dept_id
       AND status = 'ACTIVE';

    g_cache_dept_count := v_count;

    RETURN v_count;
  END GET_DEPT_HEADCOUNT;

  -- ---------------------------------------------------------------
  -- ADJUST_SALARY
  -- Applies a percentage change to an employee's salary. Calls
  -- GET_DEPT_HEADCOUNT for a (questionable) business rule check.
  -- ---------------------------------------------------------------
  PROCEDURE ADJUST_SALARY(
    p_emp_id      IN NUMBER,
    p_pct_change  IN NUMBER
  ) IS
    v_dept_id NUMBER;
    v_headcount NUMBER;
  BEGIN
    SELECT dept_id INTO v_dept_id FROM employees WHERE emp_id = p_emp_id;

    v_headcount := GET_DEPT_HEADCOUNT(v_dept_id);

    IF v_headcount > 200 THEN
      RAISE_APPLICATION_ERROR(-20003, 'Salary adjustments frozen for large depts');
    END IF;

    UPDATE employees
       SET salary = salary * (1 + p_pct_change / 100)
     WHERE emp_id = p_emp_id;

    LOG_AUDIT_EVENT(p_emp_id, 'SALARY_ADJUST', 'pct=' || p_pct_change);

    COMMIT;
  END ADJUST_SALARY;

  -- ---------------------------------------------------------------
  -- RUN_DYNAMIC_REPORT
  -- LANDMINE: builds and executes SQL from string concatenation of
  -- caller-supplied table/column/value. Classic dynamic SQL, and a
  -- SQL-injection risk to boot. No safe automatic REST equivalent.
  -- ---------------------------------------------------------------
  PROCEDURE RUN_DYNAMIC_REPORT(
    p_table_name  IN VARCHAR2,
    p_filter_col  IN VARCHAR2,
    p_filter_val  IN VARCHAR2
  ) IS
    v_sql   VARCHAR2(4000);
    v_cursor SYS_REFCURSOR;
  BEGIN
    v_sql := 'SELECT * FROM ' || p_table_name ||
             ' WHERE ' || p_filter_col || ' = ''' || p_filter_val || '''';

    OPEN v_cursor FOR v_sql;
    CLOSE v_cursor;

    LOG_AUDIT_EVENT(NULL, 'DYNAMIC_REPORT', v_sql);
  END RUN_DYNAMIC_REPORT;

  -- ---------------------------------------------------------------
  -- LOG_AUDIT_EVENT
  -- AUTONOMOUS TRANSACTION: commits its own audit row independent
  -- of the caller's transaction, so audit trail survives a caller
  -- rollback. Sets package-level g_last_audit_ts as a side effect.
  -- ---------------------------------------------------------------
  PROCEDURE LOG_AUDIT_EVENT(
    p_emp_id      IN NUMBER,
    p_event_type  IN VARCHAR2,
    p_details     IN VARCHAR2
  ) IS
    PRAGMA AUTONOMOUS_TRANSACTION;
  BEGIN
    INSERT INTO audit_log (emp_id, event_type, details, event_ts)
    VALUES (p_emp_id, p_event_type, p_details, SYSDATE);

    g_last_audit_ts := SYSDATE;

    COMMIT;
  END LOG_AUDIT_EVENT;

END HR_PKG;
/
