from pathlib import Path
from textwrap import dedent

from app.adapters.base import LanguageAdapter, register_adapter


class PythonAdapter(LanguageAdapter):
    name = "python"
    file_extensions = (".py",)
    docker_image = "python:3.12-slim"

    def build_command(self, project_root: Path) -> list[str]:
        # Simple byte-compilation as a "build" step
        return ["python", "-m", "compileall", str(project_root)]

    def test_command(self, project_root: Path) -> list[str]:
        # Assumes pytest is available in the container
        return ["pytest", "-q", "tests"]

    def run_contract_test_command(self, behavior_id: int, contract_id: int) -> list[str]:
        """
        This is a placeholder; later you'll implement a proper contract runner.
        """
        return [
            "python",
            "-m",
            "app.scripts.run_contract",  # to be implemented in later phase
            "--language",
            "python",
            "--behavior-id",
            str(behavior_id),
            "--contract-id",
            str(contract_id),
        ]

    def generate_test_code_from_contract(self, contract: dict, output_path: Path) -> None:
        """
        Generate a minimal pytest contract test file.
        """
        cases = contract.get("test_cases", {}).get("cases", [])
        behavior_name = contract.get("name", "behavior")

        code = dedent(
            f"""
            import pytest

            # TODO: import the target behavior implementation
            # from my_module import {behavior_name}

            CASES = {cases!r}

            @pytest.mark.parametrize("case", CASES)
            def test_{behavior_name}_contract(case):
                # input_data = case["input"]
                # expected = case["expect"]
                # result = {behavior_name}(**input_data)
                # assert result == expected
                assert "input" in case and "expect" in case
            """
        ).lstrip()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(code, encoding="utf-8")

    def generate_skeleton_from_behavior(self, behavior: dict, contract: dict, output_path: Path) -> None:
        behavior_name = contract.get("name", "behavior")
        description = contract.get("description") or behavior.get("description") or ""

        code = f'''"""
{description}
"""

def {behavior_name}(**kwargs):
    """
    TODO: Implement behavior based on contract and behavior metadata.
    """
    raise NotImplementedError("Not implemented yet")
'''
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(code, encoding="utf-8")


register_adapter(PythonAdapter)
