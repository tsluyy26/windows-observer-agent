"""Temporary process payloads for the offline Windows Job Object tests."""

import json
import os
import socket
import subprocess
import sys
import time


def launch(mode, *, output=None):
    return subprocess.Popen(
        [sys._base_executable, "-I", "-B", __file__, mode],
        stdout=output,
    )


mode = sys.argv[1]
if mode == "orphan":
    child = launch("middle", output=subprocess.PIPE)
    assert child.stdout is not None
    grandchild = child.stdout.readline().decode("ascii").strip()
    print(f"{child.pid},{grandchild}", flush=True)
elif mode == "middle":
    print(launch("hold", output=subprocess.DEVNULL).pid, flush=True)
    time.sleep(60)
elif mode == "hold":
    time.sleep(60)
elif mode == "normal":
    child = launch("short")
    print(child.pid, flush=True)
    assert child.wait(timeout=10) == 0
elif mode == "short":
    time.sleep(2)
elif mode == "exited-child":
    assert launch("done").wait(timeout=10) == 0
    print("child-exited", flush=True)
    time.sleep(60)
elif mode == "unrelated":
    print("ready", flush=True)
    time.sleep(60)
elif mode == "listener":
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        print(f"{listener.getsockname()[1]},{os.getpid()}", flush=True)
        time.sleep(60)
elif mode == "arguments":
    print(json.dumps(sys.argv[2:]), flush=True)
elif mode != "done":
    raise ValueError("Unknown fixture mode")
