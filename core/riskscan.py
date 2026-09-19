"""
riskscan.py
-----------
Hardcoded rules pass over extracted PL/SQL units, flagging constructs
that can't be safely auto-migrated to a stateless REST scaffold.
Deliberately NOT an LLM call: fast, deterministic, and every finding
traces back to an exact rule you can point at in the demo, rather than
a model's opinion.

Four rules, matching the risk categories named in the project brief:
  DYNAMIC_SQL     -- EXECUTE IMMEDIATE, or OPEN <cursor> FOR <non-literal>
  AUTONOMOUS_TXN  -- PRAGMA AUTONOMOUS_TRANSACTION
  PACKAGE_STATE   -- reads/writes to package-level spec variables
  DBMS_CALL       -- any DBMS_<PACKAGE> reference, severity varies by package
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.extractor import Unit, extract_package_variables


@dataclass
class RiskFinding:
    unit_name: str
    rule: str          # "DYNAMIC_SQL" | "AUTONOMOUS_TXN" | "PACKAGE_STATE" | "DBMS_CALL"
    severity: str       # "HIGH" | "MEDIUM" | "LOW"
    message: str
    evidence: str       # the specific line/snippet that triggered the rule

    def to_dict(self) -> dict:
        return {
            "unit_name": self.unit_name,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


# DBMS_* packages that are pure server-side concurrency/scheduling/
# security concerns with no REST equivalent at all -> HIGH.
# Packages that are informational/logging only -> LOW.
_DBMS_HIGH = {
    "DBMS_LOCK", "DBMS_SCHEDULER", "DBMS_JOB", "DBMS_ALERT",
    "DBMS_PIPE", "DBMS_CRYPTO", "DBMS_RLS", "DBMS_SESSION",
}
_DBMS_LOW = {"DBMS_OUTPUT"}


def _find_dynamic_sql(unit: Unit) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    body = unit.body

    for m in re.finditer(r"EXECUTE\s+IMMEDIATE\b.*", body, re.IGNORECASE):
        snippet = m.group(0).splitlines()[0].strip()[:120]
        findings.append(RiskFinding(
            unit_name=unit.name,
            rule="DYNAMIC_SQL",
            severity="HIGH",
            message=(
                "EXECUTE IMMEDIATE builds and runs SQL at runtime. Cannot be "
                "auto-migrated to a static REST call without knowing every "
                "shape the generated SQL can take; if any part of the string "
                "comes from caller input, this is also a SQL-injection risk "
                "in the original code."
            ),
            evidence=snippet,
        ))

    # OPEN <cursor> FOR <var> -- dynamic cursor. Distinguish from a
    # static `OPEN cursor_name;` (no FOR) or `OPEN cur FOR SELECT ...`
    # (a static, literal query) by requiring FOR to be followed by an
    # identifier that is NOT the start of a SELECT statement.
    for m in re.finditer(r"OPEN\s+\w+\s+FOR\s+(\w+)\s*;", body, re.IGNORECASE):
        target = m.group(1)
        if target.upper() == "SELECT":
            continue
        snippet = m.group(0).strip()[:120]
        findings.append(RiskFinding(
            unit_name=unit.name,
            rule="DYNAMIC_SQL",
            severity="HIGH",
            message=(
                f"`OPEN ... FOR {target}` opens a cursor against a SQL string "
                "assembled elsewhere in this unit, not a static query. Trace "
                f"back where `{target}` is built -- if it concatenates caller "
                "parameters, treat this the same as EXECUTE IMMEDIATE."
            ),
            evidence=snippet,
        ))

    return findings


def _find_autonomous_txn(unit: Unit) -> list[RiskFinding]:
    if re.search(r"PRAGMA\s+AUTONOMOUS_TRANSACTION", unit.body, re.IGNORECASE):
        return [RiskFinding(
            unit_name=unit.name,
            rule="AUTONOMOUS_TXN",
            severity="MEDIUM",
            message=(
                "This unit commits independently of its caller's transaction. "
                "A REST scaffold that doesn't preserve this will change "
                "behavior: e.g. an audit row that used to survive a caller "
                "rollback may now be lost, or a caller that expects this "
                "commit's side effects to be visible immediately may not see "
                "them under a different transaction model."
            ),
            evidence="PRAGMA AUTONOMOUS_TRANSACTION",
        )]
    return []


def _find_package_state(unit: Unit, package_vars: list[str]) -> list[RiskFinding]:
    if not package_vars:
        return []
    findings: list[RiskFinding] = []
    for var in package_vars:
        # Match the variable as a whole word so e.g. G_COUNT doesn't
        # match G_COUNT_TOTAL.
        pattern = re.compile(rf"\b{re.escape(var)}\b", re.IGNORECASE)
        matches = list(pattern.finditer(unit.body))
        if not matches:
            continue
        # Distinguish read from write by checking for `VAR :=` nearby.
        is_write = any(
            re.search(rf"\b{re.escape(var)}\s*:=", unit.body[m.start():m.start() + len(var) + 10], re.IGNORECASE)
            for m in matches
        )
        findings.append(RiskFinding(
            unit_name=unit.name,
            rule="PACKAGE_STATE",
            severity="MEDIUM",
            message=(
                f"{'Writes to' if is_write else 'Reads'} package-level variable "
                f"`{var}`, which persists across calls within a session in "
                "PL/SQL but has no equivalent under a stateless REST call. "
                "Migrating this correctly requires deciding where that state "
                "lives now (request payload, database row, cache) -- it "
                "cannot be dropped silently without changing behavior."
            ),
            evidence=f"{var} referenced in {unit.name}",
        ))
    return findings


def _find_dbms_calls(unit: Unit) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    seen: set[str] = set()
    for m in re.finditer(r"\bDBMS_[A-Z0-9_]+\b", unit.body, re.IGNORECASE):
        pkg = m.group(0).upper()
        if pkg in seen:
            continue
        seen.add(pkg)

        if pkg in _DBMS_HIGH:
            severity = "HIGH"
            message = (
                f"{pkg} is a server-side concurrency/scheduling/security "
                "primitive with no direct REST equivalent. This needs a "
                "deliberate redesign decision, not a mechanical port."
            )
        elif pkg in _DBMS_LOW:
            severity = "LOW"
            message = f"{pkg} is server-side console output only -- safe to drop or replace with normal logging."
        else:
            severity = "MEDIUM"
            message = (
                f"{pkg} is an Oracle built-in package call with no guaranteed "
                "REST equivalent -- review what it's used for before assuming "
                "it can be dropped or trivially replaced."
            )

        findings.append(RiskFinding(
            unit_name=unit.name,
            rule="DBMS_CALL",
            severity=severity,
            message=message,
            evidence=pkg,
        ))
    return findings


def scan_risks(units: list[Unit], source_text: str | None = None) -> list[RiskFinding]:
    """Run all rules against every unit.

    source_text: the raw .sql source (needed once, not per-unit) to
    extract package-level variable names for the PACKAGE_STATE rule.
    If omitted, PACKAGE_STATE is skipped (no variables to check against).
    """
    package_vars = extract_package_variables(source_text) if source_text else []

    findings: list[RiskFinding] = []
    for unit in units:
        findings.extend(_find_dynamic_sql(unit))
        findings.extend(_find_autonomous_txn(unit))
        findings.extend(_find_package_state(unit, package_vars))
        findings.extend(_find_dbms_calls(unit))
    return findings


_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def sort_findings(findings: list[RiskFinding]) -> list[RiskFinding]:
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.unit_name, f.rule))


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    from core.extractor import extract_from_file

    target = sys.argv[1] if len(sys.argv) > 1 else "samples/legacy_hr_package.sql"
    units = extract_from_file(target)
    source = Path(target).read_text(encoding="utf-8", errors="replace")
    findings = sort_findings(scan_risks(units, source_text=source))
    print(json.dumps([f.to_dict() for f in findings], indent=2))
