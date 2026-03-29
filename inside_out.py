#!/usr/bin/env python3
"""Inside-Out Claude Code: an interactive AI shell that runs your system."""

import json
import os
import subprocess
import sys
import tempfile
import readline  # enables arrow keys / history in input()

MODEL = os.environ.get("INSIDE_OUT_MODEL", "claude-sonnet-4-5-20250929")
MAX_TOKENS = 8192
ANT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ant")
MAX_ROUNDS = 15

SYSTEM_PROMPT = f"""You are an AI assistant with direct access to the user's system via tools.
You can run bash commands, execute Python scripts, read/write files, and list directories.
You are running on: {os.uname().sysname} {os.uname().machine}
Working directory: {os.getcwd()}

Guidelines:
- Use tools proactively to answer questions. Don't say "I would run..." - just run it.
- For multi-step tasks, chain tool calls across rounds.
- Show key results to the user, but don't dump huge outputs without summarizing.
- If a command fails, diagnose and retry with a fix.
- Ask for confirmation before destructive operations (rm -rf, overwriting files, etc).
"""

# ─── Tool Definitions ───────────────────────────────────────────────

TOOLS = [
    {
        "name": "bash",
        "description": "Run a bash command on the user's system. Returns stdout and stderr. Use for: installing packages, git, system info, networking, file operations, or any shell command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "python",
        "description": "Execute a Python script. Use print() for output. Good for: data processing, calculations, web scraping, file manipulation, or anything that benefits from Python's stdlib.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                }
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file from disk.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path to read",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates parent directories if needed. Overwrites existing files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: current directory)",
                }
            },
            "required": [],
        },
    },
]

# ─── Tool Executors ──────────────────────────────────────────────────

BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def exec_bash(command):
    print(f"  {DIM}$ {command}{RESET}")
    try:
        r = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=60,
            cwd=os.getcwd(),
        )
        out = r.stdout + r.stderr
        if r.returncode != 0:
            out += f"\n[exit code: {r.returncode}]"
        return out.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 60s"


def exec_python(code):
    print(f"  {DIM}[python script]{RESET}")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        f.flush()
        try:
            r = subprocess.run(
                ["python3", f.name],
                capture_output=True, text=True, timeout=60,
            )
            out = r.stdout
            if r.returncode != 0:
                out += f"\nSTDERR:\n{r.stderr}\n[exit code: {r.returncode}]"
            return out.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: script timed out after 60s"
        finally:
            os.unlink(f.name)


def exec_read_file(path):
    path = os.path.expanduser(path)
    print(f"  {DIM}reading: {path}{RESET}")
    try:
        with open(path, "r") as f:
            content = f.read()
        if len(content) > 10000:
            content = content[:10000] + f"\n... [truncated, {len(content)} bytes total]"
        return content or "(empty file)"
    except Exception as e:
        return f"ERROR: {e}"


def exec_write_file(path, content):
    path = os.path.expanduser(path)
    print(f"  {DIM}writing: {path} ({len(content)} bytes){RESET}")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"ERROR: {e}"


def exec_list_dir(path="."):
    path = os.path.expanduser(path or ".")
    print(f"  {DIM}ls: {path}{RESET}")
    try:
        entries = sorted(os.listdir(path))
        result = []
        for e in entries:
            full = os.path.join(path, e)
            prefix = "d " if os.path.isdir(full) else "f "
            result.append(prefix + e)
        return "\n".join(result) or "(empty directory)"
    except Exception as e:
        return f"ERROR: {e}"


def execute_tool(name, input_data):
    if name == "bash":
        return exec_bash(input_data["command"])
    elif name == "python":
        return exec_python(input_data["code"])
    elif name == "read_file":
        return exec_read_file(input_data["path"])
    elif name == "write_file":
        return exec_write_file(input_data["path"], input_data["content"])
    elif name == "list_directory":
        return exec_list_dir(input_data.get("path", "."))
    else:
        return f"Unknown tool: {name}"


# ─── Ant CLI Interface ──────────────────────────────────────────────

def call_ant(messages):
    cmd = [
        ANT, "--format", "json",
        "messages", "create",
        "--model", MODEL,
        "--max-tokens", str(MAX_TOKENS),
        "--system", json.dumps(SYSTEM_PROMPT),
    ]
    for tool in TOOLS:
        cmd += ["--tool", json.dumps(tool)]
    cmd += ["--tool-choice", '{"type":"auto"}']
    for msg in messages:
        cmd += ["--message", json.dumps(msg)]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ant failed: {r.stderr}")
    return json.loads(r.stdout)


# ─── Main Loop ───────────────────────────────────────────────────────

def process_turn(messages):
    """Run one full turn: call Claude, execute tools, loop until end_turn."""
    for round_num in range(1, MAX_ROUNDS + 1):
        try:
            response = call_ant(messages)
        except Exception as e:
            print(f"\n{RED}Error calling Claude: {e}{RESET}")
            return

        content = response.get("content", [])
        stop_reason = response.get("stop_reason")

        tool_results = []
        for block in content:
            if block["type"] == "text" and block["text"].strip():
                print(f"\n{BOLD}{block['text']}{RESET}")
            elif block["type"] == "tool_use":
                tool_name = block["name"]
                tool_input = block["input"]
                print(f"\n{YELLOW}[tool: {tool_name}]{RESET}")
                output = execute_tool(tool_name, tool_input)
                # Show truncated output
                display = output if len(output) <= 500 else output[:500] + "..."
                print(f"{GREEN}{display}{RESET}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": output[:15000],  # cap what we send back
                })

        if stop_reason == "end_turn" or not tool_results:
            return

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": tool_results})

    print(f"\n{DIM}(reached {MAX_ROUNDS} tool rounds, stopping){RESET}")


def main():
    global MODEL

    print(f"""{BOLD}
 ╔══════════════════════════════════════╗
 ║   Inside-Out Claude Code            ║
 ║   AI-driven shell • model: {MODEL:<9s}║
 ╚══════════════════════════════════════╝{RESET}
 Type your request. Claude will use tools to help.
 Commands: 'quit' to exit, 'clear' to reset history, 'model <name>' to switch.
""")

    messages = []

    while True:
        try:
            user_input = input(f"{BLUE}you>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break
        if user_input.lower() == "clear":
            messages = []
            print(f"{DIM}(conversation cleared){RESET}")
            continue
        if user_input.lower().startswith("model "):
            MODEL = user_input.split(None, 1)[1]
            print(f"{DIM}(switched to {MODEL}){RESET}")
            continue

        messages.append({"role": "user", "content": user_input})
        process_turn(messages)

        # Add a synthetic assistant message for history if the last message is user
        # (the actual response is shown but we need to track it for context)
        # Re-call to get the final response for history tracking
        # Actually, let's just keep going with what we have - the tool results
        # are already in the messages list from process_turn


if __name__ == "__main__":
    main()
