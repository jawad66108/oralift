import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.extractor import extract_from_file
from core.specgen import (
    _render_prompt,
    generate_spec,
    generate_specs_cached,
    SpecGenError,
)


FIXTURE = Path(__file__).parent.parent / "samples" / "legacy_hr_package.sql"


def _get_unit(name: str):
    units = {u.name: u for u in extract_from_file(FIXTURE)}
    return units[name]


def test_render_prompt_substitutes_all_placeholders():
    unit = _get_unit("GET_DEPT_HEADCOUNT")
    prompt = _render_prompt(unit)
    assert "{{ " not in prompt  # nothing left unsubstituted
    assert "{%" not in prompt
    assert "GET_DEPT_HEADCOUNT" in prompt
    assert "HR_PKG" in prompt
    assert "Returns: NUMBER" in prompt
    assert "p_dept_id" in prompt


def test_render_prompt_omits_returns_line_for_procedures():
    unit = _get_unit("HIRE_EMPLOYEE")  # a PROCEDURE, no return type
    prompt = _render_prompt(unit)
    assert "Returns:" not in prompt


def test_generate_spec_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    unit = _get_unit("HIRE_EMPLOYEE")
    with pytest.raises(SpecGenError, match="ANTHROPIC_API_KEY"):
        generate_spec(unit, api_key=None)


def test_generate_spec_returns_text_on_successful_mocked_call():
    unit = _get_unit("HIRE_EMPLOYEE")

    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "This procedure hires a new employee."
    fake_response = MagicMock()
    fake_response.content = [fake_block]

    with patch("anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = fake_response
        result = generate_spec(unit, api_key="sk-ant-fake-key-for-test")

    assert result == "This procedure hires a new employee."


def test_generate_specs_cached_skips_units_already_in_cache():
    unit = _get_unit("HIRE_EMPLOYEE")
    cache_key = (unit.name, hash(unit.body))
    cache = {cache_key: "cached spec text"}

    results = generate_specs_cached([unit], cache, model="claude-sonnet-5")
    assert results["HIRE_EMPLOYEE"] == "cached spec text"


def test_generate_specs_cached_records_error_without_raising(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    unit = _get_unit("HIRE_EMPLOYEE")
    cache: dict = {}

    results = generate_specs_cached([unit], cache)
    assert results["HIRE_EMPLOYEE"].startswith("ERROR:")
    # the error itself is now cached too, so a re-run doesn't retry
    cache_key = (unit.name, hash(unit.body))
    assert cache[cache_key].startswith("ERROR:")
