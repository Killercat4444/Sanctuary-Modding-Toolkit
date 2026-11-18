"""Invoked by the existing addon in an isolated Blender background process."""
from pathlib import Path
import sys
import importlib

if __name__ == '__main__':
    folder=Path(__file__).resolve().parent
    sys.path.insert(0,str(folder.parent))
    module=importlib.import_module(folder.name+'.vfx_catalogue')
    module.run_job(sys.argv[sys.argv.index('--')+1])
