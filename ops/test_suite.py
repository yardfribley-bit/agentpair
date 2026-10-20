"""Discover a suite; report unavailable desktop dependencies as explicit skips."""
import importlib.util
from pathlib import Path
import sys
import unittest

folder, top = map(Path,sys.argv[1:])
loader = unittest.TestLoader()
suite = unittest.TestSuite(loader.loadTestsFromName('tests.'+p.stem) for p in sorted(folder.glob('test_*.py'))) if folder==Path(__file__).resolve().parents[1]/'tests' else loader.discover(str(folder),pattern='test_*.py',top_level_dir=str(top))


def optional_desktop(tests):
    output = unittest.TestSuite()
    for test in tests:
        if isinstance(test,unittest.TestSuite):
            output.addTest(optional_desktop(test))
        elif isinstance(test,unittest.loader._FailedTest) and "No module named 'PySide6'" in str(getattr(test,'_exception','')) and importlib.util.find_spec('PySide6') is None:
            @unittest.skip('Optional desktop dependency PySide6 is not installed')
            def unavailable():pass
            output.addTest(unittest.FunctionTestCase(unavailable,description=str(test)))
        else:output.addTest(test)
    return output

class DesktopOptionalResult(unittest.TextTestResult):
    def addError(self,test,err):
        if isinstance(err[1],ModuleNotFoundError) and (err[1].name or '').split('.')[0]=='PySide6':
            self.addSkip(test,'Optional desktop dependency PySide6 is not installed')
        else:super().addError(test,err)

result=unittest.TextTestRunner(verbosity=1,resultclass=DesktopOptionalResult).run(optional_desktop(suite))
raise SystemExit(not result.wasSuccessful())
