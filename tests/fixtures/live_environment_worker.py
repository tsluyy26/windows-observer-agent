"""A synthetic service payload that checks the environment received from Win32."""

import ctypes
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


mode, key_digest, system_digest = sys.argv[1:]
expected = {
    "SYSTEMROOT", "WINDIR", "USERPROFILE", "LOCALAPPDATA", "TEMP", "TMP",
    "WINDOWS_MCP_AUTH_KEY", "ANONYMIZED_TELEMETRY", "WINDOWS_MCP_WATCHDOG",
    "FASTMCP_STREAMABLE_HTTP_PATH", "FASTMCP_ENV_FILE",
}
assert set(os.environ) == expected, "Unexpected child environment"
assert hashlib.sha256(os.environ["WINDOWS_MCP_AUTH_KEY"].encode("ascii")).hexdigest() == key_digest
assert os.environ["WINDOWS_MCP_AUTH_KEY"] not in " ".join(sys.argv), "Token in arguments"
assert os.environ["ANONYMIZED_TELEMETRY"] == "false"
assert os.environ["WINDOWS_MCP_WATCHDOG"] == "off"
assert os.environ["FASTMCP_STREAMABLE_HTTP_PATH"] == "/mcp"
assert os.environ["FASTMCP_ENV_FILE"] == r"\\.\NUL"
assert not Path(os.environ["FASTMCP_ENV_FILE"]).is_file()

assert os.environ["SYSTEMROOT"] == os.environ["WINDIR"]
assert os.environ["TEMP"] == os.environ["TMP"]
paths = [os.environ[key] for key in ("SYSTEMROOT", "USERPROFILE", "LOCALAPPDATA", "TEMP")]
actual_digest = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()
assert actual_digest == system_digest, "Windows paths did not come from the expected locations"
assert all(Path(value).is_dir() for value in paths)
assert Path.home() == Path(os.environ["USERPROFILE"])
assert Path(tempfile.gettempdir()) == Path(os.environ["TEMP"])
assert bool(ctypes.WinDLL("kernel32"))

if mode == "venv":
    assert sys.prefix != sys.base_prefix, "The venv redirector did not select its venv"
else:
    assert sys.prefix == sys.base_prefix, "Expected the base interpreter"

if mode != "descendant":
    result = subprocess.run(
        [sys._base_executable, "-I", "-B", __file__, "descendant", key_digest, system_digest],
        capture_output=True, encoding="ascii", timeout=10,
    )
    assert result.returncode == 0 and result.stdout.strip() == "S7_CHILD_PASS", "Descendant environment differs"

print("S7_CHILD_PASS")
