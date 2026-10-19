"""The record lock admits one writer per team record, in and across processes."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


ADAPTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))
import lead
from adapter import Refusal


CONTENDER = ("import sys\n"
             "sys.path.insert(0, sys.argv[1])\n"
             "from pathlib import Path\n"
             "import lead\n"
             "from adapter import Refusal\n"
             "try:\n"
             "    with lead.record_lock(Path(sys.argv[2])):\n"
             "        print('acquired')\n"
             "except Refusal as error:\n"
             "    print(error.code)\n")


class RecordLockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lock-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def contend(self):
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-I", "-c", CONTENDER, str(ADAPTER), str(self.root)],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            self.fail("record lock blocked a second process instead of refusing it")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def contend_in_process(self):
        # A lock that blocks instead of refusing must fail the test, not hang the suite, and the test must not
        # touch process timers the caller owns. The holder releases when its block ends, so a blocked thread
        # then finishes and the cleanup join reaps it.
        outcome = []

        def attempt():
            try:
                with lead.record_lock(self.root):
                    outcome.append("acquired")
            except Refusal as error:
                outcome.append(error.code)
            except Exception as error:
                outcome.append(repr(error))

        thread = threading.Thread(target=attempt, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 30)
        thread.join(5)
        return outcome[0] if outcome else "blocked"

    def test_second_writer_in_process_is_refused_while_held(self):
        with lead.record_lock(self.root):
            outcome = self.contend_in_process()
        self.assertEqual(outcome, "lead_in_progress")

    def test_second_writer_in_another_process_is_refused_while_held(self):
        with lead.record_lock(self.root):
            self.assertEqual(self.contend(), "lead_in_progress")
        self.assertEqual(self.contend(), "acquired", "a released lock must be takeable by a new process")

    def test_lock_is_released_when_the_holder_raises(self):
        with self.assertRaises(RuntimeError):
            with lead.record_lock(self.root):
                raise RuntimeError("holder crashed mid-turn")
        with lead.record_lock(self.root):
            pass

    def test_refused_contender_does_not_disturb_the_holder(self):
        with lead.record_lock(self.root):
            self.assertEqual(self.contend(), "lead_in_progress")
            self.assertEqual(self.contend_in_process(), "lead_in_progress")
            self.assertEqual(self.contend(), "lead_in_progress", "the holder still owns the record")


if __name__ == "__main__":
    unittest.main()
