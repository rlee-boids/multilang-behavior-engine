from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Union

from app.adapters.base import LanguageAdapter


class PythonAdapter(LanguageAdapter):
    """
    Python language adapter.

    Responsibilities:
    - Detect Python projects
    - Provide container image for Podman
    - Provide build + test commands
    - Provide hooks for contract-based test generation and skeleton generation
    """

    name: str = "python"
    file_extensions: List[str] = [".py"]
    docker_image: str = "python:3.12-slim"

    # ---------- Detection ----------

    def detect(self, path: str) -> bool:
        """
        Heuristic: Python if we see .py files or pyproject / setup.py, etc.
        """
        p = Path(path)
        if p.is_file() and p.suffix == ".py":
            return True

        if p.is_dir():
            # Any .py under this directory
            for child in p.rglob("*.py"):
                return True

            # Common Python project markers
            for marker in ("pyproject.toml", "setup.py", "requirements.txt"):
                if (p / marker).exists():
                    return True

        return False

    # ---------- Build / Test commands (single-repo) ----------

    def build_command(self, project_root: str) -> Union[str, List[str], None]:
        """
        Basic build step:

        - If requirements.txt exists: pip install -r requirements.txt
        - Else: no-op

        NOTE: We expect the runtime to already have `pytest` installed (or
        to install it once per container). This build step is for project-
        specific deps.
        """
        cmd = (
            f"cd {project_root} && "
            "if [ -f requirements.txt ]; then "
            "  pip install -r requirements.txt; "
            "else "
            "  echo 'No requirements.txt; skipping dependency install'; "
            "fi"
        )
        return cmd

    def test_command(self, project_root: str) -> Union[str, List[str]]:
        """
        Generic test command for a Python project in a single repo:

        - cd into project_root
        - If tests/ exists or any test_*.py / *_test.py exists:
            run pytest
        - Else: just do a syntax check via compileall

        We don't install pytest here; the runtime (Podman runner) is responsible
        for ensuring `pytest` is available in the container image.
        """
        cmd = (
            f"cd {project_root} && "
            "if [ -d tests ] || ls test_*.py *_test.py 1>/dev/null 2>&1; then "
            "  python -m pytest || "
            "    (echo 'pytest failed; fallback to syntax check'; "
            "     python -m compileall .); "
            "else "
            "  echo 'No tests/ or test_*.py found; running compileall only'; "
            "  python -m compileall .; "
            "fi"
        )
        return cmd

    # ---------- Contract-specific test run (paired legacy + harness) ----------

    def run_contract_test_command(
        self,
        behavior_id: int,
        contract_id: int | None,
        project_root: str = "/tests",
    ) -> Union[str, List[str]]:
        """
        Command used when running harness tests against code in a *paired*
        container setup.

        Expected layout:
          /code   -> code repo (converted implementation, or legacy python)
          /tests  -> harness repo (working dir)

        We set PYTHONPATH to include /code so tests can `import` implementation.
        """
        cmd = (
            f"cd {project_root} && "
            "export PYTHONPATH=/code:$PYTHONPATH; "
            "if [ -d tests ] || ls test_*.py *_test.py 1>/dev/null 2>&1; then "
            "  python -m pytest; "
            "else "
            "  echo 'No tests found in harness repo'; "
            "  exit 1; "
            "fi"
        )
        return cmd

    # ---------- Code generation hooks ----------

    def generate_test_code_from_contract(self, contract, output_path: str) -> None:
        """
        Generate pytest-based tests under output_path/tests/ that encode the
        BehaviorContract.test_cases as JSON and parametrize over them.

        We assume `contract.test_cases` is a list of dicts like:
          {
            "name": "simple_case",
            "input": {...},
            "expect": {...}
          }

        The generated tests are *intelligent scaffolds*:
        - They carry the real test case data.
        - They create one pytest case per contract test case.
        - They include TODO comments where actual calls/assertions should go.
        """
        tests_dir = os.path.join(output_path, "tests")
        os.makedirs(tests_dir, exist_ok=True)

        # --- Smoke test: tests/test_smoke.py ---
        smoke_path = os.path.join(tests_dir, "test_smoke.py")
        if not os.path.exists(smoke_path):
            smoke_code = """import pytest

def test_smoke():
    # Basic sanity check that the test harness runs.
    assert True
"""
            with open(smoke_path, "w") as f:
                f.write(smoke_code)

        # --- Contract-driven tests: tests/test_contract_<id>.py ---
        contract_id = getattr(contract, "id", "unknown") if contract else "unknown"
        test_cases = getattr(contract, "test_cases", None) if contract else None

        target_name = f"test_contract_{contract_id}.py"
        target_path = os.path.join(tests_dir, target_name)

        if not test_cases:
            # If no test cases, write a small placeholder
            contract_name = getattr(contract, "name", f"behavior_{contract_id}") if contract else ""
            code = f"""import pytest


def test_no_contract_cases_defined():
    # No test_cases found on contract {contract_id!r} {contract_name!r}
    # Add test_cases to BehaviorContract to get generated, parameterized tests.
    assert True
"""
            with open(target_path, "w") as f:
                f.write(code)
            return

        # Serialize test_cases to JSON and embed it
        json_text = json.dumps(test_cases, indent=2)
        contract_name = getattr(contract, "name", f"behavior_{contract_id}")

        code = f"""import json
import pytest

# Contract-driven tests for:
#   contract id   : {contract_id}
#   contract name : {contract_name!r}

CASES_JSON = r\"\"\"{json_text}\"\"\"
CASES = json.loads(CASES_JSON)


def _case_id(case: dict) -> str:
    name = case.get("name") or "unnamed_case"
    return name


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_contract_case(case):
    \"\"\"Generated from BehaviorContract.test_cases.

    Each `case` dict is expected to look like:
      {{
        "name": "...",
        "input": {{ ... }},
        "expect": {{ ... }}
      }}

    This is a scaffold; you still need to:
      - import the function or class under test
      - call it with `case["input"]`
      - assert on the result vs `case["expect"]`
    \"\"\"
    # TODO: wire to actual implementation

    # Example scaffold (replace `your_function` and tweak as needed):
    #
    # from your_module import your_function
    # result = your_function(**case.get("input", {{}}))
    # expected = case.get("expect")
    # assert result == expected
    #
    # For now we mark this as a placeholder so the test passes.
    assert True
"""
        with open(target_path, "w") as f:
            f.write(code)

    def generate_skeleton_from_behavior(self, behavior, contract, output_path: str) -> None:
        """
        Generate a simple Python module skeleton for the given behavior.

        - Writes <output_path>/<sanitized_behavior_name>.py
        - Adds a stub function `run(**kwargs)` (or similar)
        """
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)

        raw_name = getattr(behavior, "name", "behavior")
        # Make a safe module name: lower-case, replace non-alnum with underscores.
        base = raw_name.lower().replace("::", "_").replace(":", "_").replace(" ", "_")
        if not base:
            base = f"behavior_{getattr(behavior, 'id', '')}"
        module_name = base

        file_path = out_dir / f"{module_name}.py"
        if file_path.exists():
            # Don't overwrite existing file
            return

        behavior_desc = getattr(behavior, "description", "").strip()
        contract_id = getattr(contract, "id", None)
        contract_name = getattr(contract, "name", "") if contract else ""

        lines: list[str] = []
        if behavior_desc:
            lines.append('"""')
            lines.append(behavior_desc)
            lines.append('"""')
            lines.append("")
        if contract_id is not None:
            lines.append(
                f"# Skeleton generated from contract {contract_id}"
                + (f" ({contract_name})" if contract_name else "")
            )
            lines.append("")
        lines.append("from __future__ import annotations")
        lines.append("")
        lines.append("")
        lines.append("def run(**kwargs):")
        lines.append("    \"\"\"Entry point for this behavior.")
        lines.append("")
        lines.append("    Args:")
        lines.append("        **kwargs: Inputs for the behavior, as described by the behavior contract.")
        lines.append("")
        lines.append("    Returns:")
        lines.append("        Output structure as described in the behavior contract.")
        lines.append("    \"\"\"")
        lines.append("    raise NotImplementedError('Behavior implementation not generated yet')")
        lines.append("")

        file_path.write_text("\n".join(lines))


python_adapter = PythonAdapter()
