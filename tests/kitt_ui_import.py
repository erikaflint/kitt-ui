from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_server_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "server.py"
    spec = spec_from_file_location("kitt_ui_server", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module
