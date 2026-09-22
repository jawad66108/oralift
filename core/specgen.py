"""
specgen.py
----------
Generates a plain-English spec for one PL/SQL unit at a time, via a
single Groq API call per unit. Prompt template lives in prompts/spec.md
so it can be tuned without touching this file.

Groq's API is OpenAI-compatible (same request/response shape), so
this uses the `openai` SDK pointed at Groq's base URL rather than a
separate groq-specific package -- one less dependency, and it means
switching providers again later is a one-parameter change.

Requires GROQ_API_KEY in the environment. If it's missing, spec
generation is skipped with a clear message rather than crashing the
whole app -- the rest of OraLift's pipeline (extraction, graph, risk
scan) works with zero API keys, and specs are the one stage that
costs money per unit, so failing soft here matters for a demo.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.extractor import Unit

_DEFAULT_MODEL = "openai/gpt-oss-120b"  # Groq's general-purpose text model
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "spec.md"


class SpecGenError(Exception):
    """Raised when spec generation can't proceed (missing key, API error)."""


def _render_prompt(unit: Unit) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")

    params_block = "\n".join(
        f"  - {p.name} ({p.direction} {p.datatype})" for p in unit.params
    ) or "  (none)"

    # Minimal manual templating -- the prompt file uses {{ var }}
    # placeholders but this is intentionally not a full Jinja2 render
    # (that's reserved for scaffold.py's code templates) since the
    # prompt has no loops/conditionals that need real templating.
    rendered = (
        template
        .replace("{{ unit_type }}", unit.unit_type)
        .replace("{{ unit_name }}", unit.name)
        .replace("{{ package_name }}", unit.package or "(none)")
        .replace("{{ params_block }}", params_block)
        .replace("{{ body }}", unit.body)
    )

    if unit.return_type:
        rendered = rendered.replace(
            "{% if return_type %}Returns: {{ return_type }}{% endif %}",
            f"Returns: {unit.return_type}",
        )
    else:
        rendered = rendered.replace(
            "{% if return_type %}Returns: {{ return_type }}{% endif %}", ""
        )

    return rendered


def generate_spec(unit: Unit, model: str = _DEFAULT_MODEL, api_key: str | None = None) -> str:
    """One LLM call, returns the plain-English spec as markdown text.

    Raises SpecGenError if GROQ_API_KEY is not set or the API call
    fails -- callers (app.py) should catch this and show a graceful
    message rather than letting the whole page crash.
    """
    key = api_key or os.environ.get("GROQ_API_KEY")
    if not key:
        raise SpecGenError(
            "GROQ_API_KEY is not set. Export it before running the app "
            "(export GROQ_API_KEY=gsk_...) to enable spec generation."
        )

    try:
        import openai
    except ImportError as e:
        raise SpecGenError(
            "The 'openai' package is not installed. Run: pip install openai"
        ) from e

    client = openai.OpenAI(api_key=key, base_url=_GROQ_BASE_URL)
    prompt = _render_prompt(unit)

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
    except openai.APIError as e:
        raise SpecGenError(f"Groq API call failed: {e}") from e

    return (response.choices[0].message.content or "").strip()


def generate_specs_cached(units: list[Unit], cache: dict, model: str = _DEFAULT_MODEL) -> dict[str, str]:
    """Generate specs for all units, skipping any already present in
    `cache` (keyed by (unit.name, hash(unit.body))) so re-running the
    app on the same file doesn't re-spend API calls. `cache` is
    mutated in place and expected to be something persistent across
    reruns, e.g. st.session_state['spec_cache'] in app.py.

    Returns {unit.name: spec_text}. Units that fail (including a
    missing API key) get a spec_text starting with "ERROR:" instead
    of raising, so one bad unit doesn't stop the rest.
    """
    results: dict[str, str] = {}
    for unit in units:
        cache_key = (unit.name, hash(unit.body))
        if cache_key in cache:
            results[unit.name] = cache[cache_key]
            continue
        try:
            spec = generate_spec(unit, model=model)
        except SpecGenError as e:
            spec = f"ERROR: {e}"
        cache[cache_key] = spec
        results[unit.name] = spec
    return results


if __name__ == "__main__":
    import sys
    from core.extractor import extract_from_file

    target = sys.argv[1] if len(sys.argv) > 1 else "samples/legacy_hr_package.sql"
    unit_name = sys.argv[2] if len(sys.argv) > 2 else None

    units = extract_from_file(target)
    if unit_name:
        units = [u for u in units if u.name == unit_name.upper()]
        if not units:
            print(f"No unit named {unit_name} found.")
            sys.exit(1)

    for u in units:
        print(f"=== {u.name} ===")
        try:
            print(generate_spec(u))
        except SpecGenError as e:
            print(f"ERROR: {e}")
        print()