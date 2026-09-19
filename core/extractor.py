"""
extractor.py
------------
Regex-based extraction of PL/SQL units (procedures & functions) from
Oracle .sql source files. Deliberately NOT a real grammar/parser --
this is a 48-hour hackathon tool, and PL/SQL's grammar is not worth
fighting in that window. The approach:

  1. Strip comments (-- line comments and /* */ block comments) so
     they don't pollute call-site detection or confuse regexes.
  2. Find PACKAGE ... AS/IS ... END; blocks (if any) to know the
     package name each unit belongs to.
  3. Within package bodies (or a bare file with no package wrapper),
     find each `PROCEDURE name(...) IS/AS ... END name;` and
     `FUNCTION name(...) RETURN type IS/AS ... END name;` block.
  4. Parse the parameter list for each unit (name, direction, type).
  5. Scan each unit's body for call sites: identifiers immediately
     followed by `(` that aren't PL/SQL keywords, and aren't the
     unit's own name.

This is intentionally permissive: legacy Oracle code is inconsistent
about whitespace, casing, and how bodies close. Regexes use re.IGNORECASE
and re.DOTALL where a construct can span multiple lines.

Known limitations (documented, not hidden -- this is exactly the kind
of thing riskscan.py should also be able to flag structurally):
  - Nested procedures/functions (declared inside another unit's
    DECLARE section) are not extracted as separate units.
  - Overloaded units (same name, different signature) will collide
    in the unit-name-keyed lookups used by grapher.py; last one wins.
  - Package-body-only files (no spec) are supported, but a spec-only
    file will produce units with empty bodies.
  - String literals containing text that looks like a PL/SQL keyword
    or a call site are not specially escaped; this is a known source
    of false positives that riskscan.py's dynamic-SQL check actually
    *wants* to catch (since dynamic SQL lives inside string literals).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# PL/SQL reserved words / built-ins that commonly appear as `word(`
# but are NOT calls to another procedure/function in this codebase.
# Not exhaustive -- extend as false positives show up in testing.
_KEYWORD_CALLS = {
    "if", "elsif", "while", "for", "loop", "case", "when", "then",
    "select", "insert", "update", "delete", "into", "from", "where",
    "values", "and", "or", "not", "in", "is", "as", "begin", "end",
    "exception", "raise", "return", "declare", "cursor", "procedure",
    "function", "package", "body", "pragma", "commit", "rollback",
    "savepoint", "execute", "open", "close", "fetch", "bulk",
    "count", "sum", "avg", "min", "max", "nvl", "nvl2", "decode",
    "to_char", "to_date", "to_number", "trunc", "round", "substr",
    "instr", "length", "replace", "trim", "upper", "lower", "sysdate",
    "raise_application_error", "dbms_output",  # reported separately by riskscan
    # Common Oracle datatypes that take a size/precision in parens,
    # e.g. `v_sql VARCHAR2(4000)` -- not a call.
    "varchar2", "nvarchar2", "number", "char", "nchar", "raw",
    "float", "long", "clob", "blob", "date", "timestamp",
}

_PARAM_DIRECTIONS = ("IN OUT", "IN", "OUT")


@dataclass
class Param:
    name: str
    direction: str  # "IN", "OUT", "IN OUT"
    datatype: str


@dataclass
class Unit:
    name: str
    unit_type: str          # "PROCEDURE" | "FUNCTION"
    package: Optional[str]  # containing package name, or None
    params: list[Param] = field(default_factory=list)
    return_type: Optional[str] = None  # functions only
    body: str = ""
    calls: list[str] = field(default_factory=list)
    source_file: str = ""
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.unit_type,
            "package": self.package,
            "params": [
                {"name": p.name, "direction": p.direction, "datatype": p.datatype}
                for p in self.params
            ],
            "return_type": self.return_type,
            "body": self.body,
            "calls": self.calls,
            "source_file": self.source_file,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


def _strip_comments(sql: str) -> str:
    """Remove -- line comments and /* */ block comments.

    Does not attempt to distinguish comment markers that occur inside
    string literals (e.g. a VARCHAR2 containing '--'); this is a known
    false-positive source, acceptable for the hackathon scope.
    """
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    return sql


def _find_package_name(sql: str) -> Optional[str]:
    m = re.search(
        r"CREATE\s+OR\s+REPLACE\s+PACKAGE\s+(?:BODY\s+)?([A-Za-z0-9_\$#]+)",
        sql,
        re.IGNORECASE,
    )
    return m.group(1).upper() if m else None


def extract_package_variables(sql_text: str) -> list[str]:
    """Extract package-level variable names declared in a PACKAGE
    *spec* block (not the BODY): lines like `g_foo DATE;` or
    `g_bar NUMBER := 0;` that sit between `PACKAGE <name> AS/IS` and
    the matching `END <name>;`, excluding PROCEDURE/FUNCTION
    declarations (which have their own handling in extract_units).

    Needed by riskscan.py's PACKAGE_STATE rule: a unit that reads or
    writes one of these names has a side effect that doesn't map
    cleanly onto a stateless REST call.
    """
    cleaned = _strip_comments(sql_text)

    spec_match = re.search(
        r"CREATE\s+OR\s+REPLACE\s+PACKAGE\s+(?!BODY\b)([A-Za-z0-9_\$#]+)\s+(?:AS|IS)\b",
        cleaned,
        re.IGNORECASE,
    )
    if not spec_match:
        return []

    pkg_name = spec_match.group(1)
    spec_start = spec_match.end()
    end_match = re.search(
        rf"\bEND\s+{re.escape(pkg_name)}\s*;",
        cleaned[spec_start:],
        re.IGNORECASE,
    )
    spec_body = cleaned[spec_start: spec_start + end_match.start()] if end_match else cleaned[spec_start:]

    variables: list[str] = []
    for line in spec_body.splitlines():
        line = line.strip().rstrip(",")
        if not line or not line.endswith(";"):
            continue
        if re.search(r"\b(PROCEDURE|FUNCTION|PRAGMA)\b", line, re.IGNORECASE):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_\$#]*)\s+[A-Za-z]", line)
        if m:
            variables.append(m.group(1).upper())

    return variables


_UNIT_HEADER_RE = re.compile(
    r"""
    (?P<type>PROCEDURE|FUNCTION)\s+
    (?P<name>[A-Za-z0-9_\$#]+)\s*
    (?P<params>\((?:[^()]|\([^()]*\))*\))?   # balanced-ish single-level paren group
    \s*
    (?:RETURN\s+(?P<rettype>[A-Za-z0-9_\.\%]+))?
    \s*(?:IS|AS)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _split_params(param_block: Optional[str]) -> list[Param]:
    if not param_block:
        return []
    inner = param_block.strip()
    if inner.startswith("("):
        inner = inner[1:]
    if inner.endswith(")"):
        inner = inner[:-1]
    if not inner.strip():
        return []

    # Split on top-level commas only (params rarely nest parens in
    # legacy code, but guard anyway).
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)

    params: list[Param] = []
    for raw in parts:
        raw = raw.strip()
        if not raw:
            continue
        tokens = raw.split(None, 1)
        if not tokens:
            continue
        name = tokens[0]
        rest = tokens[1] if len(tokens) > 1 else ""

        direction = "IN"  # PL/SQL default when unspecified
        rest_upper = rest.upper()
        for d in _PARAM_DIRECTIONS:
            if rest_upper.startswith(d):
                direction = d
                rest = rest[len(d):].strip()
                break

        # Strip a trailing default value (":= expr" or "DEFAULT expr")
        rest = re.split(r"\bDEFAULT\b|:=", rest, maxsplit=1, flags=re.IGNORECASE)[0]
        datatype = rest.strip() or "UNKNOWN"

        params.append(Param(name=name, direction=direction, datatype=datatype))

    return params


def _extract_calls(body: str, own_name: str) -> list[str]:
    """Find identifiers used as calls: `identifier(` not preceded by
    a dot-qualifier issue and not a keyword/own-name.

    Two deliberate exclusions beyond the keyword list:
      - `INTO tablename (col, col) VALUES (...)` -- the table name in
        an INSERT's column list is not a procedure/function call.
      - identifiers immediately preceded by `.` are left in (dotted
        calls like PKG.PROC are common and worth keeping), but the
        qualifier itself is not re-checked separately.
    """
    calls: set[str] = set()
    for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_\$#]*)\s*\(", body):
        ident = m.group(1)
        low = ident.lower()
        if low in _KEYWORD_CALLS:
            continue
        if ident.upper() == own_name.upper():
            continue

        preceding = body[:m.start()].rstrip()
        prev_word_match = re.search(r"([A-Za-z_][A-Za-z0-9_\$#]*)$", preceding)
        prev_word = prev_word_match.group(1).lower() if prev_word_match else ""
        if prev_word == "into":
            # INSERT INTO <table> ( ... ) -- a table reference, not a call.
            continue

        calls.add(ident.upper())
    return sorted(calls)


def _find_matching_end(sql: str, search_start: int, unit_name: str) -> int:
    """Given the index right after a unit's IS/AS, find the index of
    the matching `END [unit_name];` for that unit by tracking nested
    BEGIN/END (and CASE...END, IF...END IF, LOOP...END LOOP) depth.

    Returns the index just after the terminating semicolon, or
    len(sql) if no matching END is found (malformed/truncated input).
    """
    # Tokens that open a nested block requiring an extra END, and
    # tokens that are an END variant that DON'T close the unit itself
    # (END IF, END LOOP, END CASE) -- these must be consumed together
    # with their following keyword so a bare END isn't miscounted.
    token_re = re.compile(
        r"""
        \b(?P<begin>BEGIN)\b
        |\b(?P<case>CASE)\b
        |\bEND\s+(?P<endkw>IF|LOOP|CASE)\b
        |\b(?P<endbare>END)\b
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    depth = 0  # incremented by this unit's own opening BEGIN, below
    pos = search_start
    for m in token_re.finditer(sql, search_start):
        if m.group("begin") or m.group("case"):
            depth += 1
        elif m.group("endkw"):
            continue  # paired with its own IF/LOOP/CASE, not a block END
        elif m.group("endbare"):
            depth -= 1
            if depth == 0:
                # consume up to the following semicolon
                semi = sql.find(";", m.end())
                return semi + 1 if semi != -1 else len(sql)
        pos = m.end()
    return len(sql)


def extract_units(sql_text: str, source_file: str = "") -> list[Unit]:
    """Extract all PROCEDURE/FUNCTION units from a PL/SQL source string."""
    cleaned = _strip_comments(sql_text)
    package_name = _find_package_name(cleaned)

    units: list[Unit] = []
    for m in _UNIT_HEADER_RE.finditer(cleaned):
        # Skip forward-declarations inside a PACKAGE (spec) block --
        # those have no body, just `IS`/`AS` directly followed by `;`.
        # Heuristic: if the very next non-whitespace char after the
        # header match is `;`, treat as a spec-only declaration.
        after = cleaned[m.end():].lstrip()
        if after.startswith(";"):
            continue

        body_start = m.end()
        body_end = _find_matching_end(cleaned, body_start, m.group("name"))
        body = cleaned[body_start:body_end]

        params = _split_params(m.group("params"))
        calls = _extract_calls(body, m.group("name"))

        start_line = cleaned.count("\n", 0, m.start()) + 1
        end_line = cleaned.count("\n", 0, body_end) + 1

        units.append(
            Unit(
                name=m.group("name").upper(),
                unit_type=m.group("type").upper(),
                package=package_name,
                params=params,
                return_type=(m.group("rettype") or "").upper() or None,
                body=body.strip(),
                calls=calls,
                source_file=source_file,
                start_line=start_line,
                end_line=end_line,
            )
        )

    return units


def extract_from_file(path: str | Path) -> list[Unit]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return extract_units(text, source_file=str(path))


def extract_from_folder(folder: str | Path, pattern: str = "*.sql") -> list[Unit]:
    folder = Path(folder)
    units: list[Unit] = []
    for f in sorted(folder.glob(pattern)):
        units.extend(extract_from_file(f))
    return units


if __name__ == "__main__":
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "samples/legacy_hr_package.sql"
    result = extract_from_file(target)
    print(json.dumps([u.to_dict() for u in result], indent=2))
