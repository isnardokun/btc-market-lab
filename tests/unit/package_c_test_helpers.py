import tempfile
from pathlib import Path

def new_root(testcase):
    folder=tempfile.TemporaryDirectory(prefix='package-c-test-')
    testcase.addCleanup(folder.cleanup)
    return Path(folder.name)/'package-a-fixture-c-run'
