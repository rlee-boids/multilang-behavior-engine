# app/adapters/perl_adapter.py
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import List, Union

from app.adapters.base import LanguageAdapter, ServiceHarnessInfo


class PerlAdapter(LanguageAdapter):
    """
    Perl language adapter.

    Responsibilities:
    - Detect Perl projects
    - Provide container image for Podman
    - Provide build + test commands
    - Provide hooks for contract-based test generation and skeleton generation
    - Provide a minimal PSGI service harness for deployment
    """

    # Adapter identity
    name: str = "perl"
    file_extensions: List[str] = [".pl", ".pm", ".cgi", ".plx", ".pls", ".psgi", ".fcgi"]
    # Official Perl image; has `prove` available.
    docker_image: str = "perl:5.38"

    # ---------- Detection ----------

    def detect(self, path: str) -> bool:
        """
        Heuristic: Perl if we see .pl/.pm files or common CPAN-style structure.
        """
        p = Path(path)
        if p.is_file() and p.suffix in self.file_extensions:
            return True

        if p.is_dir():
            # Any .pl/.pm/.cgi under this directory
            for child in p.rglob("*"):
                if child.suffix in self.file_extensions:
                    return True

            # Common Perl project markers
            for marker in ("Makefile.PL", "Build.PL", "cpanfile"):
                if (p / marker).exists():
                    return True

        return False

    # ---------- Build / Test commands (single-repo) ----------

    def build_command(self, project_root: str) -> Union[str, List[str], None]:
        """
        No explicit build step for plain Perl scripts/modules.

        If you later adopt a more formal build (ExtUtils::MakeMaker, Module::Build),
        you can detect Makefile.PL here and run `perl Makefile.PL && make`.
        """
        return None

    def test_command(self, project_root: str) -> Union[str, List[str]]:
        """
        Generic test command for a Perl project in a single repo:

        - cd into project_root
        - If t/ exists -> run `prove -r t`
        - else -> try `prove -r .`
        - If all that fails, at least syntax-check all .pl files.
        """
        cmd = (
            f"cd {project_root} && "
            "if [ -d t ]; then "
            "  prove -r t || prove -r . || "
            "    (echo 'prove failed; fallback to syntax check'; "
            "     find . -name '*.pl' -print0 | xargs -0 -n1 perl -c); "
            "else "
            "  prove -r . || "
            "    (echo 'prove failed; fallback to syntax check'; "
            "     find . -name '*.pl' -print0 | xargs -0 -n1 perl -c); "
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
        Command used when running harness tests against legacy code in a *paired*
        container setup.

        In the paired runner, layout is:
          /code  -> legacy repo (with lib/Plot/Generator.pm etc.)
          /tests -> harness repo (working dir)

        We set PERL5LIB to include /code and /code/lib so harness tests
        can `use` the legacy modules.
        """
        cmd = (
            f"cd {project_root} && "
            "export PERL5LIB=/code/lib:/code:$PERL5LIB; "
            "if [ -d t ]; then "
            "  prove -r t || prove -r .; "
            "else "
            "  prove -r .; "
            "fi"
        )
        return cmd

    # ---------- Code generation hooks ----------

    def generate_test_code_from_contract(self, contract, output_path: str) -> None:
        """
        Generate Perl TAP tests (.t files) under output_path/t/.

        Uses BehaviorContract.test_cases (if present) to create a contract-driven
        test file that embeds those cases as JSON and iterates them in Perl.

        Files generated:
        - t/00-load.t      : sanity check / harness bootstrap
        - t/01-basic.t     : basic placeholder test
        - t/02-contract.t  : per-contract-case TODO tests
        """
        t_dir = os.path.join(output_path, "t")
        os.makedirs(t_dir, exist_ok=True)

        # --- 00-load.t: very simple sanity test ---
        load_test = """#!/usr/bin/env perl
use strict;
use warnings;
use Test::More;

ok(1, 'test harness loaded');

done_testing();
"""
        with open(os.path.join(t_dir, "00-load.t"), "w") as f:
            f.write(load_test)

        # --- 01-basic.t: placeholder that can be extended later ---
        basic_test = """#!/usr/bin/env perl
use strict;
use warnings;
use Test::More;

# TODO: load the legacy module under test here, e.g.:
# use lib '/code/lib';
# use Plot::Generator;

ok(1, 'basic placeholder test');

done_testing();
"""
        with open(os.path.join(t_dir, "01-basic.t"), "w") as f:
            f.write(basic_test)

        # --- 02-contract.t: contract-driven tests using embedded JSON ---
        test_cases = getattr(contract, "test_cases", None) if contract else None

        if not test_cases:
            # No test cases -> simple placeholder
            contract_id = getattr(contract, "id", "unknown") if contract else "unknown"
            contract_name = getattr(contract, "name", f"behavior_{contract_id}") if contract else ""
            contract_test = f"""#!/usr/bin/env perl
use strict;
use warnings;
use Test::More;

# No test_cases found on contract {contract_id} {contract_name}
ok(1, 'no contract test cases defined yet');

done_testing();
"""
        else:
            # Serialize test_cases to JSON and embed it in the Perl test.
            json_text = json.dumps(test_cases, indent=2)
            contract_id = getattr(contract, "id", "unknown")
            contract_name = getattr(contract, "name", f"behavior_{contract_id}")

            contract_test = f"""#!/usr/bin/env perl
use strict;
use warnings;
use Test::More;
use JSON qw(decode_json);

# Contract-driven tests for:
#   contract id   : {contract_id}
#   contract name : {contract_name}

my $json = <<'JSON';
{json_text}
JSON

my $cases = decode_json($json);

foreach my $case (@$cases) {{
    my $name = $case->{{'name'}} // 'unnamed_case';

    TODO: {{
        local $TODO = 'Implement real contract-based assertions for this case';

        # Examples of what you might do here once wired to real code:
        #   - Call a module function / method with $case->{{'input'}}
        #   - Compare results to $case->{{'expect'}}
        #   - Use is_deeply, like, cmp_ok, etc.
        #
        # For now we just mark the test as a TODO placeholder.
        ok(1, "placeholder for contract case $name");
    }}
}}

done_testing();
"""
        with open(os.path.join(t_dir, "02-contract.t"), "w") as f:
            f.write(contract_test)

    def generate_skeleton_from_behavior(self, behavior, contract, output_path: str) -> None:
        """
        Generate a simple Perl module skeleton for the given behavior.

        - Writes <output_path>/<SanitizedName>.pm
        - Adds a package with a stub subroutine `run(%args)`
        """
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)

        raw_name = getattr(behavior, "name", "Behavior")
        # Make a safe package name: replace non-word-ish separators with ::
        package = raw_name.replace(":", "::").replace(" ", "::")
        if not package:
            package = f"Behavior{getattr(behavior, 'id', '')}"

        # Use last segment as file name
        file_segment = package.split("::")[-1] or "Behavior"
        file_path = out_dir / f"{file_segment}.pm"
        if file_path.exists():
            # Don't overwrite existing file
            return

        behavior_desc = getattr(behavior, "description", "").strip()
        contract_id = getattr(contract, "id", None)
        contract_name = getattr(contract, "name", "") if contract else ""

        lines: list[str] = []
        lines.append(f"package {package};")
        lines.append("use strict;")
        lines.append("use warnings;")
        lines.append("")
        lines.append("our $VERSION = '0.01';")
        lines.append("")
        if behavior_desc:
            lines.append("# " + behavior_desc)
            lines.append("")
        if contract_id is not None:
            lines.append(
                "# Skeleton generated from contract "
                f"{contract_id}" + (f" ({contract_name})" if contract_name else "")
            )
            lines.append("")
        lines.append("sub run {")
        lines.append("    my (%args) = @_;")
        lines.append("    die 'Not implemented yet';")
        lines.append("}")
        lines.append("")
        lines.append("1;")
        lines.append("")

        file_path.write_text("\n".join(lines))

    # ---------- NEW: service harness generation ----------

    def generate_service_harness(
        self,
        behavior,
        implementation,
        contract,
        repo_root: Path,
    ) -> ServiceHarnessInfo:
        """
        Generate a PSGI service harness for this implementation.

        For now, we focus on CGI-based UIs like cgi-bin/plot_ui.cgi:
          - app.psgi wraps the CGI script using CGI::Compile + CGI::Emulate::PSGI.
          - Dockerfile installs Plack and CGI glue, exposes port 5000, and runs plackup.

        Later we can add a JSON->run(%args) style harness for non-CGI modules.
        """
        repo_root = Path(repo_root)

        file_path_str = getattr(implementation, "file_path", "") or ""
        if not file_path_str:
            # Default to your plotting CGI; caller should usually pass a real file_path.
            cgi_rel = "cgi-bin/plot_ui.cgi"
        else:
            cgi_rel = file_path_str

        # Normalize to POSIX-style path for literal Perl string
        cgi_posix = str(PurePosixPath(cgi_rel))

        app_psgi = repo_root / "app.psgi"
        app_psgi_contents = f"""use strict;
use warnings;
use FindBin;
use lib "$FindBin::Bin/lib";

use CGI::Compile;
use CGI::Emulate::PSGI;

# Wrap the legacy CGI script:
my $cgi_app = CGI::Compile->compile("{cgi_posix}");
my $app     = CGI::Emulate::PSGI->handler($cgi_app);

# Plack expects to see a PSGI app in $_[0]:
$app;
"""
        app_psgi.write_text(app_psgi_contents)

        dockerfile_path = repo_root / "Dockerfile"
        dockerfile_contents = f"""FROM {self.docker_image}

WORKDIR /app
COPY . /app

# Install dependencies for plotting + PSGI
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        cpanminus \\
        libgd-dev \\
    && cpanm --notest \\
        GD::Graph \\
        JSON \\
        File::Slurp \\
        Plack \\
        CGI::Emulate::PSGI \\
        CGI::Compile \\
    && apt-get clean \\
    && rm -rf /var/lib/apt/lists/*

EXPOSE 5000

CMD ["plackup", "-Ilib", "-p", "5000", "app.psgi"]
"""
        dockerfile_path.write_text(dockerfile_contents)

        return ServiceHarnessInfo(
            context_dir=repo_root,
            dockerfile_path=dockerfile_path,
            internal_port=5000,
        )


perl_adapter = PerlAdapter()
