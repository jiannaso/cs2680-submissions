"""Unit tests for the tool executors. Run: python3 -m unittest discover tests"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools import READ_WINDOW, ToolError, Tools  # noqa: E402


def git(wd, *args):
    subprocess.run(["git", "-C", wd, "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = os.path.realpath(self.tmp.name)
        self.write("pkg/mod.py", "def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x + 1\n")
        git(self.wd, "init", "-q")
        git(self.wd, "add", "-A")
        git(self.wd, "commit", "-qm", "base")
        self.t = Tools(self.wd)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text, newline=None):
        full = os.path.join(self.wd, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", newline=newline) as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.wd, rel), newline="") as f:
            return f.read()

    # bash
    def test_bash_runs_in_workdir_and_reports_exit_code(self):
        self.assertIn(self.wd, self.t.bash("pwd"))
        self.assertIn("[exit code 3]", self.t.bash("exit 3"))

    def test_bash_does_not_see_api_key(self):
        old = os.environ.get("CS2680_API_KEY")
        os.environ["CS2680_API_KEY"] = "hyi-test"
        try:
            self.assertNotIn("hyi-test", self.t.bash("env"))
        finally:
            if old is None:
                del os.environ["CS2680_API_KEY"]
            else:
                os.environ["CS2680_API_KEY"] = old

    def test_bash_blocks_git_commit(self):
        with self.assertRaises(ToolError):
            self.t.bash("git add -A && git commit -m x")

    # read_file
    def test_read_file_windows_large_files(self):
        self.write("big.txt", "".join(f"line {i}\n" for i in range(1, 1001)))
        out = self.t.read_file("big.txt")
        self.assertIn(f"lines 1-{READ_WINDOW} of 1000", out)
        self.assertIn(f"continue with start_line={READ_WINDOW + 1}", out)
        self.assertIn("line 500", self.t.read_file("big.txt", 495, 505))

    def test_read_file_lists_directories_and_rejects_outside_paths(self):
        self.assertIn("mod.py", self.t.read_file("pkg"))
        with self.assertRaises(ToolError):
            self.t.read_file("/etc/passwd")

    # edit_file
    def test_edit_requires_unique_match(self):
        with self.assertRaises(ToolError) as cm:
            self.t.edit_file("pkg/mod.py", "    return x + 1", "    return x + 2")
        self.assertIn("occurs 2 times", str(cm.exception))
        self.t.edit_file("pkg/mod.py", "def g(x):\n    return x + 1", "def g(x):\n    return x + 2")
        self.assertIn("return x + 2", self.read("pkg/mod.py"))

    def test_edit_preserves_crlf(self):
        self.write("win.txt", "a\r\nb\r\nc\r\n", newline="")
        self.t.edit_file("win.txt", "b\nc", "B\nC")
        self.assertEqual(self.read("win.txt"), "a\r\nB\r\nC\r\n")

    def test_edit_strips_copied_line_numbers(self):
        out = self.t.edit_file("pkg/mod.py", "     1\tdef f(x):\n     2\t    return x + 1",
                               "     1\tdef f(x):\n     2\t    return x * 2")
        self.assertIn("removed read_file line-number prefixes", out)
        self.assertTrue(self.read("pkg/mod.py").startswith("def f(x):\n    return x * 2\n"))

    def test_edit_not_found_points_at_closest_match(self):
        with self.assertRaises(ToolError) as cm:
            self.t.edit_file("pkg/mod.py", "def g(x):\n  return x + 1", "def g(x):\n  return 0")
        self.assertIn("closest match is lines 5-6", str(cm.exception))

    def test_edit_warns_on_python_syntax_error(self):
        out = self.t.edit_file("pkg/mod.py", "def f(x):", "def f(x:")
        self.assertIn("no longer parses", out)

    def test_edit_creates_new_file(self):
        self.t.edit_file("pkg/new.py", "", "X = 1\n")
        self.assertEqual(self.read("pkg/new.py"), "X = 1\n")
        with self.assertRaises(ToolError):
            self.t.edit_file("pkg/new.py", "", "X = 2\n")

    # done
    def test_done_pushes_back_once_on_empty_diff(self):
        with self.assertRaises(ToolError):
            self.t.done()
        self.assertIn("Task marked as done", self.t.done())

    def test_done_accepts_new_untracked_file(self):
        self.t.edit_file("pkg/new.py", "", "X = 1\n")
        out = self.t.done()
        self.assertIn("pkg/new.py", out)
        # done must not leave anything staged
        staged = subprocess.run(["git", "-C", self.wd, "diff", "--cached", "--name-only"],
                                capture_output=True, text=True).stdout
        self.assertEqual(staged, "")

    # repro_check
    def test_repro_check_flags_a_check_that_already_passes(self):
        out = self.t.repro_check("true", "trivial")
        self.assertIn("PASSES", out)
        self.assertIn("does not reproduce", out)

    def test_done_reruns_repro_checks(self):
        check = "python3 -c 'import sys; sys.path.insert(0, \"pkg\"); import mod; assert mod.f(1) == 3'"
        self.assertIn("FAILS", self.t.repro_check(check, "f(1) == 3"))
        self.t.edit_file("pkg/mod.py", "def f(x):\n    return x + 1", "def f(x):\n    return x + 5")
        with self.assertRaises(ToolError) as cm:
            self.t.done()
        self.assertIn("still fail", str(cm.exception))
        self.t.edit_file("pkg/mod.py", "return x + 5", "return x + 2")
        self.assertIn("1/1 pass", self.t.done())

    def test_done_asks_for_one_final_review_when_task_text_given(self):
        t = Tools(self.wd, task_text="## Requirements\n- f must return x + 2")
        t.edit_file("pkg/mod.py", "def f(x):\n    return x + 1", "def f(x):\n    return x + 2")
        with self.assertRaises(ToolError) as cm:
            t.done()
        self.assertIn("f must return x + 2", str(cm.exception))
        self.assertIn("pkg/mod.py", str(cm.exception))
        with self.assertRaises(ToolError) as cm:  # done again without checking anything
            t.done()
        self.assertIn("without checking anything", str(cm.exception))
        self.assertIn("Task marked as done", t.done())  # pushed back only once

    def test_done_after_review_accepted_once_something_was_checked(self):
        t = Tools(self.wd, task_text="## Requirements\n- f must return x + 2")
        t.edit_file("pkg/mod.py", "def f(x):\n    return x + 1", "def f(x):\n    return x + 2")
        with self.assertRaises(ToolError):
            t.done()
        t.execute("read_file", {"path": "pkg/mod.py"})  # tools are called through execute()
        self.assertIn("Task marked as done", t.done())

    def test_done_skips_review_when_budget_is_low(self):
        t = Tools(self.wd, task_text="## Requirements\n- anything")
        t.edit_file("pkg/mod.py", "return x + 1\n\n\ndef g", "return x + 3\n\n\ndef g")
        t.budget_low = True
        self.assertIn("Task marked as done", t.done())

    def test_new_ts_module_shows_how_nearby_tests_import(self):
        self.write("packages/metrics/tests/MetricsApi.test.ts",
                   "import MetricsApi from '../lib/MetricsApi';\n\ndescribe('x', () => {});\n")
        out = self.t.edit_file("packages/metrics/lib/observeApiError.ts", "", "export const x = 1;\n")
        self.assertIn("import MetricsApi from '../lib/MetricsApi'", out)

    def test_new_python_module_shows_test_imports(self):
        self.write("tests/test_mod.py", "from pkg.mod import f\nimport pytest\n")
        out = self.t.edit_file("pkg/new.py", "", "X = 1\n")
        self.assertIn("from pkg.mod import f", out)
        self.assertNotIn("How existing tests", self.t.edit_file("tests/test_new.py", "", "Y = 1\n"))

    def test_done_asks_for_default_export_when_tests_use_default_imports(self):
        self.write("packages/metrics/tests/Counter.test.ts", "import Counter from '../lib/Counter';\n")
        self.t.edit_file("packages/metrics/lib/observeApiError.ts", "", "export function observeApiError() {}\n")
        with self.assertRaises(ToolError) as cm:
            self.t.done()
        self.assertIn("packages/metrics/lib/observeApiError.ts", str(cm.exception))
        self.t.edit_file("packages/metrics/lib/observeApiError.ts", "export function observeApiError() {}\n",
                         "export function observeApiError() {}\nexport default observeApiError;\n")
        self.assertIn("Task marked as done", self.t.done())


if __name__ == "__main__":
    unittest.main()
