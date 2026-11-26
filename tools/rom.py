"""Read and check the supplied Zoids Saga ROM."""

import hashlib
import subprocess


ROM_SHA1 = "75d8c15ac281ea93c8ac7cc7641c490799557081"
ROM_SIZE = 0x800000
BASE = 0x08000000


def read_rom(path):
    data = path.read_bytes()
    if len(data) != ROM_SIZE or hashlib.sha1(data).hexdigest() != ROM_SHA1:
        raise ValueError("ROM hash does not match Zoids Saga (Japan), revision 0.")
    return data


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
