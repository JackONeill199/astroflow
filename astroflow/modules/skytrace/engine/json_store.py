"""Atomic JSON publication for concurrent report readers and settings writers."""
import json
import os
from pathlib import Path
import tempfile
import time


def open_read(path):
    for attempt in range(21):
        try: return Path(path).open('rb')
        except PermissionError:
            if attempt==20: raise
            time.sleep(.01)


def read_json(path,default=None):
    try:
        with open_read(path) as source: return json.load(source)
    except (OSError,ValueError): return default


def replace_file(source,destination):
    for attempt in range(21):
        try:
            os.replace(source,destination)
            return
        except PermissionError:
            if attempt==20: raise
            time.sleep(.025)


def write_json(path,value):
    path=Path(path)
    name=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as output:
            name=output.name
            json.dump(value,output,ensure_ascii=False,indent=2,allow_nan=False)
        replace_file(name,path)
    finally:
        if name: Path(name).unlink(missing_ok=True)
