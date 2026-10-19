"""Run `mifal doctor` from an installed engine under an audit hook: log each opened path and each
subprocess, and refuse any socket connection. Usage: audited_doctor.py LOG ENGINE_LIB"""
import sys

log = open(sys.argv[1], "w")


def hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        log.write(f"open {args[0]}\n")
    elif event == "subprocess.Popen":
        log.write(f"exec {args[1]}\n")
    elif event == "socket.connect":
        log.write(f"connect {args[1]}\n")
        log.flush()
        raise RuntimeError("the doctor opened a network connection")
    log.flush()


sys.addaudithook(hook)
sys.path.insert(0, sys.argv[2])
from mifal.cli import main  # noqa: E402

sys.exit(main(["doctor"]))
