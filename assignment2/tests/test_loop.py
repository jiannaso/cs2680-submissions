"""Offline tests of the agentic loop with a scripted fake model. Run: python3 -m unittest discover tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import agentic_loop  # noqa: E402
from src.logger import RunLogger  # noqa: E402


def call(name, cid="c", **args):
    return NS(id=cid, function=NS(name=name, arguments=json.dumps(args)))


def reply(*tool_calls, content="", finish="tool_calls"):
    msg = NS(content=content, tool_calls=list(tool_calls) or None)
    usage = NS(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return NS(choices=[NS(message=msg, finish_reason=finish if tool_calls else (finish if finish != "tool_calls" else "stop"))],
              usage=usage)


class FakeClient:
    """Replays scripted replies and records every messages list it was sent."""
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, model, messages, **kw):
        self.sent.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0)


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = os.path.realpath(self.tmp.name)
        with open(os.path.join(self.wd, "a.py"), "w") as f:
            f.write("X = 1\n")
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
            subprocess.run(["git", "-C", self.wd, "-c", "user.email=t@t", "-c", "user.name=t", *cmd],
                           check=True, capture_output=True)
        self.logger = RunLogger(enabled=False)

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, replies):
        client = FakeClient(replies)
        reason = agentic_loop.run_agent("fix X", self.wd, "m", self.logger, client=client)
        return reason, client

    def test_every_tool_call_gets_one_result(self):
        reason, client = self.run_with([
            reply(call("bash", "c1", command="ls"), call("read_file", "c2", path="a.py")),
            reply(call("edit_file", "c3", path="a.py", old_string="X = 1", new_string="X = 2")),
            reply(call("done", "c4", summary="ok")),  # first done: final review
            reply(call("bash", "c5", command="git diff")),  # act on the review
            reply(call("done", "c6", summary="ok")),
        ])
        self.assertEqual(reason, "done")
        last = client.sent[-1]
        call_ids = [tc["id"] for m in last if m["role"] == "assistant" for tc in m.get("tool_calls", [])]
        result_ids = [m["tool_call_id"] for m in last if m["role"] == "tool"]
        self.assertEqual(call_ids[:3], result_ids[:3])

    def test_nudges_are_consecutive_then_stop(self):
        prose = [reply(content="thinking...") for _ in range(4)]
        reason, client = self.run_with(prose)
        self.assertEqual(reason, "no_tool_calls")
        self.assertEqual(len(client.sent), 4)
        # a tool call in between resets the counter
        reason, client = self.run_with([reply(content="hm")] * 3 + [reply(call("bash", command="ls"))] +
                                       [reply(content="hm")] * 4)
        self.assertEqual(reason, "no_tool_calls")
        self.assertEqual(len(client.sent), 8)

    def test_cut_off_reply_gets_specific_nudge(self):
        reason, client = self.run_with([reply(content="x" * 50, finish="length")] + [reply(content="")] * 3)
        self.assertIn("cut off", client.sent[1][-1]["content"])

    def test_repeated_call_is_flagged(self):
        reason, client = self.run_with([reply(call("bash", command="false"))] * 3 + [reply(content="")] * 4)
        self.assertIn("exact call 3 times", client.sent[3][-1]["content"])

    def test_wrap_up_warning_near_limit(self):
        old = agentic_loop.MAX_ITERATIONS
        agentic_loop.MAX_ITERATIONS = 12
        try:
            reason, client = self.run_with([reply(call("bash", command=f"echo {i}")) for i in range(12)])
        finally:
            agentic_loop.MAX_ITERATIONS = old
        self.assertEqual(reason, "max_iterations")
        warned = [m["content"] for m in client.sent[-1] if m["role"] == "tool" and "budget" in m["content"]]
        self.assertEqual(len(warned), 1)

    def test_bad_json_arguments_become_error_result(self):
        bad = NS(id="c1", function=NS(name="bash", arguments="{not json"))
        reason, client = self.run_with([reply(bad)] + [reply(content="")] * 4)
        self.assertIn("could not parse", client.sent[1][-1]["content"])


class CompactTest(unittest.TestCase):
    def test_compact_keeps_recent_and_small_results(self):
        from src.context import compact
        msgs = [{"role": "system", "content": "S" * 5000}, {"role": "user", "content": "U" * 5000}]
        for i in range(12):
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": f"f{i}.py"})}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": ("x" * 5000) if i != 1 else "small"})
        saved = compact(msgs, keep_recent=8)
        tools = [m["content"] for m in msgs if m["role"] == "tool"]
        self.assertIn("elided", tools[0])
        self.assertIn("read_file f0.py", tools[0])
        self.assertEqual(tools[1], "small")
        self.assertTrue(all(len(t) == 5000 for t in tools[4:]))
        self.assertEqual(len(msgs[0]["content"]) + len(msgs[1]["content"]), 10000)
        self.assertGreater(saved, 0)
        self.assertEqual(compact(msgs, keep_recent=8), 0)  # idempotent


if __name__ == "__main__":
    unittest.main()
