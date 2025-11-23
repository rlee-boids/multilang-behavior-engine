from app.adapters.base import get_adapter, list_adapters  # noqa: F401

# Import adapters so they self-register
from app.adapters.python_adapter import PythonAdapter  # noqa: F401
from app.adapters.perl_adapter import PerlAdapter  # noqa: F401
