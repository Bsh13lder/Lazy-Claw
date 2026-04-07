import importlib.abc, importlib.util, os, sys

_CACHE = os.path.join(os.path.dirname(__file__), '__pycache__')
_PKG = 'mcp_taskai'

class _PycFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if not fullname.startswith(_PKG + '.'):
            return None
        parts = fullname.split('.')
        rel = parts[1:]
        if len(rel) == 1:
            pyc = os.path.join(_CACHE, f'{rel[0]}.cpython-311.pyc')
        elif len(rel) == 2 and rel[0] == 'providers':
            pyc = os.path.join(os.path.dirname(__file__), 'providers', '__pycache__', f'{rel[1]}.cpython-311.pyc')
        else:
            return None
        if not os.path.exists(pyc):
            return None
        return importlib.util.spec_from_file_location(fullname, pyc)

if not any(isinstance(f, _PycFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _PycFinder())
