from pathlib import Path
from textwrap import dedent

from app.adapters.base import LanguageAdapter, register_adapter


class PerlAdapter(LanguageAdapter):
    name = "perl"
    file_extensions = (".pl", ".pm")
    docker_image = "perl:5.38"

    def build_command(self, project_root: Path) -> list[str]:
        # Basic syntax check on .pl/.pm files
        cmd = "find . -name '*.pl' -o -name '*.pm' | xargs -r -n1 perl -c"
        return ["sh", "-c", cmd]

    def test_command(self, project_root: Path) -> list[str]:
        # Assumes prove + Test::More are installed
        return ["prove", "-r", "t"]

    def run_contract_test_command(self, behavior_id: int, contract_id: int) -> list[str]:
        # Placeholder for a Perl-based contract runner script
        return [
            "perl",
            "scripts/run_contract.pl",
            "--behavior-id",
            str(behavior_id),
            "--contract-id",
            str(contract_id),
        ]

    def generate_test_code_from_contract(self, contract: dict, output_path: Path) -> None:
        """
        Generate a minimal Test::More contract test file.
        """
        cases = contract.get("test_cases", {}).get("cases", [])
        behavior_name = contract.get("name", "behavior")

        # Keep it simple: just verify structure for now
        code = dedent(
            f"""
            use strict;
            use warnings;
            use Test::More;

            # TODO: use the target module
            # use My::Module qw({behavior_name});

            my @cases = @{{
                cases => {cases!r}
            }}{{'cases'}};

            foreach my $case (@cases) {{
                ok(exists $case->{{'input'}} && exists $case->{{'expect'}}, "contract case has input and expect");
                # TODO: call behavior and compare with expected
            }}

            done_testing();
            """
        ).lstrip()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(code, encoding="utf-8")

    def generate_skeleton_from_behavior(self, behavior: dict, contract: dict, output_path: Path) -> None:
        behavior_name = contract.get("name", "behavior")
        description = contract.get("description") or behavior.get("description") or ""

        code = dedent(
            f"""
            package Behavior::{behavior_name};

            use strict;
            use warnings;
            use Exporter 'import';

            our @EXPORT_OK = qw({behavior_name});

            # {description}

            sub {behavior_name} {{
                my (%args) = @_;
                die "Not implemented yet";
            }}

            1;
            """
        ).lstrip()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(code, encoding="utf-8")


register_adapter(PerlAdapter)
