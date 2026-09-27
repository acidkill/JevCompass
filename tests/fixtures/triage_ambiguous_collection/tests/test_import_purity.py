"""Guard the package import contract in an isolated Python process."""
from pathlib import Path
import subprocess
import sys
import unittest


class ImportPurityTests(unittest.TestCase):
    def test_import_does_not_read_runtime_files_even_if_errors_are_caught(self):
        program = r'''
from pathlib import Path
attempts = []
def forbidden_read(self, *args, **kwargs):
    attempts.append(self.name)
    raise RuntimeError("fixture-read-forbidden")
Path.read_text = forbidden_read
try:
    import cartcalc.total
except Exception:
    if attempts:
        raise SystemExit(23)
    raise
if attempts:
    raise SystemExit(23)
'''
        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, "package import must not read local runtime files")


if __name__ == "__main__":
    unittest.main()
