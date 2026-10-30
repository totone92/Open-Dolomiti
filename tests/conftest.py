"""I test usano solo parsing.py (niente Home Assistant): si carica il modulo dal suo file."""
import importlib.util
import pathlib
import sys

RADICE = pathlib.Path(__file__).resolve().parents[1]
FILE = RADICE / "custom_components" / "open_dolomiti" / "parsing.py"
spec = importlib.util.spec_from_file_location("parsing", FILE)
parsing = importlib.util.module_from_spec(spec)
sys.modules["parsing"] = parsing
spec.loader.exec_module(parsing)
