#!/usr/bin/env python3
"""Automated tool-use loop using the ant CLI with a Python executor."""

import json
import subprocess
import sys
import tempfile

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024
ANT = "./ant"

PYTHON_TOOL = {
    "name": "run_python",
    "description": "Run Python code and return the stdout output. Use print() to return results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute"}
        },
        "required": ["code"],
    },
}


def call_ant(messages):
    """Call ant CLI with messages and return parsed JSON response."""
    cmd = [
        ANT, "--format", "json",
        "messages", "create",
        "--model", MODEL,
        "--max-tokens", str(MAX_TOKENS),
        "--tool", json.dumps(PYTHON_TOOL),
        "--tool-choice", '{"type":"auto"}',
    ]
    for msg in messages:
        cmd += ["--message", json.dumps(msg)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"ant error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def run_python(code):
    """Execute Python code in a subprocess and return stdout/stderr."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        f.flush()
        try:
            result = subprocess.run(
                ["python3", f.name],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout
            if result.returncode != 0:
                output += f"\nERROR (exit {result.returncode}):\n{result.stderr}"
            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: execution timed out after 30s"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tool_loop.py 'your question here'")
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    print(f"\n>>> You: {prompt}\n")

    messages = [{"role": "user", "content": prompt}]
    max_rounds = 5

    for round_num in range(1, max_rounds + 1):
        response = call_ant(messages)
        stop_reason = response.get("stop_reason")
        content = response.get("content", [])

        # Process response blocks
        tool_results = []
        for block in content:
            if block["type"] == "text":
                print(f"<<< Claude: {block['text']}\n")
            elif block["type"] == "tool_use":
                code = block["input"]["code"]
                print(f"--- [Round {round_num}] Running Python code ---")
                print(code)
                print("---")
                output = run_python(code)
                print(f">>> Output: {output}\n")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": output,
                })

        if stop_reason == "end_turn" or not tool_results:
            break

        # Add assistant response and tool results for next round
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": tool_results})

    if round_num == max_rounds:
        print(f"(stopped after {max_rounds} rounds)")


if __name__ == "__main__":
    main()
