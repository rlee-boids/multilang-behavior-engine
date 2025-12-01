# backend/app/services/python_service_harness.py

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Tuple

from app.models.behavior_implementation import BehaviorImplementation


FASTAPI_REQUIREMENTS = [
    "fastapi",
    "uvicorn[standard]",
    "matplotlib",
]


def _ensure_requirements_txt(repo_dir: Path) -> None:
    """
    Ensure requirements.txt exists and includes the packages we need
    for the FastAPI UI harness.
    """
    req_path = repo_dir / "requirements.txt"
    existing: set[str] = set()

    if req_path.exists():
        content = req_path.read_text().splitlines()
        existing = {line.strip() for line in content if line.strip()}
    else:
        content = []

    changed = False
    for pkg in FASTAPI_REQUIREMENTS:
        if pkg not in existing:
            content.append(pkg)
            changed = True

    if changed or not req_path.exists():
        req_path.write_text("\n".join(content) + "\n")


def _ensure_app_package(repo_dir: Path, impl: BehaviorImplementation | None) -> None:
    """
    Create `app/main.py` with a simple FastAPI JSON-plot UI
    if it doesn't already exist.

    This does NOT depend on the converted code yet; it just provides
    a working service shell that we can later enhance to call into
    lib/Plot/Generator.py once the conversion is real.
    """
    app_dir = repo_dir / "app"
    app_dir.mkdir(exist_ok=True)

    init_py = app_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text("")

    main_py = app_dir / "main.py"
    if main_py.exists():
        # Don't overwrite if user already created one
        return

    behavior_name = impl.behavior.name if impl and impl.behavior else ""
    title = behavior_name or "Python JSON Plotter (converted harness)"

    code = f'''\
    from __future__ import annotations

    import base64
    import json
    from io import BytesIO
    from typing import List, Optional, Dict, Any

    import matplotlib.pyplot as plt
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel


    app = FastAPI(title={title!r})


    SAMPLE_JSON = {json.dumps({
        "x": [0, 1, 2, 3, 4, 5],
        "y": [10, 12, 18, 25, 40, 55],
        "title": "Requests over time (Python)",
        "x_label": "Hours",
        "y_label": "Requests",
    }, indent=2)}


    class PlotRequest(BaseModel):
        x: List[float]
        y: List[float]
        title: Optional[str] = "Generated Plot (Python)"
        x_label: Optional[str] = "X"
        y_label: Optional[str] = "Y"
        # extra options reserved for future use
        graph_options: Optional[Dict[str, Any]] = None


    def _make_png_data(req: PlotRequest) -> bytes:
        fig, ax = plt.subplots()
        ax.plot(req.x, req.y)
        ax.set_title(req.title or "")
        ax.set_xlabel(req.x_label or "")
        ax.set_ylabel(req.y_label or "")
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()


    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        # Simple HTML UI similar in spirit to the Perl CGI page.
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8" />
            <title>{title}</title>
            <style>
                body {{
                    font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
                    margin: 2rem;
                }}
                textarea {{
                    width: 100%;
                    height: 220px;
                    font-family: monospace;
                }}
                .error {{
                    color: red;
                    font-weight: bold;
                }}
                .plot-container {{
                    margin-top: 2rem;
                }}
                img {{
                    max-width: 100%;
                    height: auto;
                    border: 1px solid #ccc;
                }}
                button {{
                    padding: 0.5rem 1rem;
                    margin-top: 0.5rem;
                }}
            </style>
        </head>
        <body>
            <h1>{title}</h1>
            <p>Paste JSON (x, y arrays and optional labels) and click "Generate Plot".</p>
            <textarea id="json-input">{sample}</textarea><br/>
            <button onclick="generatePlot()">Generate Plot</button>
            <div id="error" class="error"></div>
            <div class="plot-container">
                <h2>Generated Plot</h2>
                <img id="plot-img" alt="Generated Plot" />
            </div>

            <script>
            async function generatePlot() {{
                const errorEl = document.getElementById("error");
                const imgEl = document.getElementById("plot-img");
                errorEl.textContent = "";
                imgEl.src = "";

                let payload;
                try {{
                    payload = JSON.parse(document.getElementById("json-input").value);
                }} catch (e) {{
                    errorEl.textContent = "Invalid JSON: " + e.message;
                    return;
                }}

                try {{
                    const resp = await fetch("/plot", {{
                        method: "POST",
                        headers: {{
                            "Content-Type": "application/json"
                        }},
                        body: JSON.stringify(payload)
                    }});
                    if (!resp.ok) {{
                        const txt = await resp.text();
                        throw new Error("HTTP " + resp.status + ": " + txt);
                    }}
                    const data = await resp.json();
                    if (data.error) {{
                        errorEl.textContent = data.error;
                        return;
                    }}
                    imgEl.src = data.image_data_uri;
                }} catch (e) {{
                    errorEl.textContent = "Request failed: " + e.message;
                }}
            }}
            </script>
        </body>
        </html>
        """.format(title={title!r}, sample=json.dumps(SAMPLE_JSON, indent=2))
    

    @app.post("/plot")
    async def plot(req: PlotRequest):
        try:
            if len(req.x) != len(req.y):
                return JSONResponse(
                    status_code=400,
                    content={{"error": "x and y must have same length"}}
                )
            png = _make_png_data(req)
            b64 = base64.b64encode(png).decode("ascii")
            data_uri = f"data:image/png;base64,{{b64}}"
            return {{"image_data_uri": data_uri}}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={{"error": f"Error generating plot: {{exc}}"}}
            )
    '''

    main_py.write_text(textwrap.dedent(code))


def _ensure_dockerfile(repo_dir: Path) -> None:
    """
    Ensure a Dockerfile exists that runs the FastAPI app with uvicorn.

    If a Dockerfile is already present, we leave it alone.
    """
    dockerfile = repo_dir / "Dockerfile"
    if dockerfile.exists():
        return

    content = """\
    FROM python:3.12-slim

    WORKDIR /app

    COPY . /app

    # Install deps (including FastAPI + uvicorn + matplotlib)
    RUN pip install --no-cache-dir -r requirements.txt

    EXPOSE 8000

    CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    """
    dockerfile.write_text(textwrap.dedent(content))


def prepare_python_service_context(
    repo_dir: Path,
    impl: BehaviorImplementation | None = None,
) -> Tuple[int, Path]:
    """
    Given a cloned converted Python repo, ensure it contains everything needed
    to run a FastAPI UI service in a container:

      - requirements.txt with FastAPI/uvicorn/matplotlib
      - app/main.py FastAPI app that renders a JSON-driven plotting UI
      - a Dockerfile that runs uvicorn on port 8000

    Returns:
        (internal_port, repo_dir)  # repo_dir is the build context
    """
    _ensure_requirements_txt(repo_dir)
    _ensure_app_package(repo_dir, impl)
    _ensure_dockerfile(repo_dir)

    internal_port = 8000
    return internal_port, repo_dir
