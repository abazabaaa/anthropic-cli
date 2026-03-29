#!/usr/bin/env python3
"""Inside-Out Claude Code: an interactive AI shell powered by Anthropic's native API tools."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import readline  # enables arrow keys / history in input()
from pathlib import Path

MODEL = os.environ.get("INSIDE_OUT_MODEL", "claude-sonnet-4-5-20250929")
MAX_TOKENS = 8192
ANT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ant")
MAX_ROUNDS = 20
MEMORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".memories")

SYSTEM_PROMPT = f"""You are an AI assistant with direct access to the user's system.
You have bash, a text editor, web search, web fetch, and persistent memory.
Running on: {os.uname().sysname} {os.uname().machine}
Working directory: {os.getcwd()}

Guidelines:
- Use tools proactively. Don't say "I would run..." - just run it.
- For multi-step tasks, chain tool calls across rounds.
- Show key results but summarize large outputs.
- If a command fails, diagnose and retry.
- Ask before destructive operations (rm -rf, overwriting files, etc).

IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE.
MEMORY PROTOCOL:
1. Use the `view` command of your `memory` tool to check for earlier progress.
2. As you make progress, record status / progress / thoughts in your memory.
ASSUME INTERRUPTION: Your context window might be reset at any moment, so record important progress in memory.
When editing your memory folder, keep its content up-to-date, coherent and organized.
"""

# ─── Native API Tool Definitions ────────────────────────────────────

# Client-side tools (we execute them)
TOOL_BASH = {"type": "bash_20250124", "name": "bash"}
TOOL_EDITOR = {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"}
TOOL_MEMORY = {"type": "memory_20250818", "name": "memory"}

# Server-side tools (Anthropic executes them)
TOOL_WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
TOOL_WEB_FETCH = {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 5}

# Custom tool (we define the schema, Claude calls it)
TOOL_SPAWN_AGENT = {
    "name": "spawn_agent",
    "description": "Spawn a sub-agent (another Claude instance) to handle a focused task. The sub-agent has the same tools (bash, editor, web search, memory) and returns its final text response. Use for: research tasks, parallel subtasks, or delegating work you want isolated from your main context. The sub-agent shares the same memory directory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "A clear, self-contained description of what the sub-agent should do",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for the sub-agent (default: same as parent)",
            },
        },
        "required": ["task"],
    },
}

ALL_TOOLS = [TOOL_BASH, TOOL_EDITOR, TOOL_MEMORY, TOOL_WEB_SEARCH, TOOL_WEB_FETCH, TOOL_SPAWN_AGENT]

# ─── Colors ─────────────────────────────────────────────────────────

BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ─── Bash Tool Executor ─────────────────────────────────────────────

def exec_bash(input_data):
    if input_data.get("restart"):
        print(f"  {DIM}(bash session restart requested){RESET}")
        return "Bash session restarted"
    command = input_data.get("command", "")
    print(f"  {DIM}$ {command}{RESET}")
    try:
        r = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=120,
            cwd=os.getcwd(),
        )
        out = r.stdout + r.stderr
        if r.returncode != 0:
            out += f"\n[exit code: {r.returncode}]"
        return out.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 120 seconds"

# ─── Text Editor Tool Executor ───────────────────────────────────────

def exec_editor(input_data):
    command = input_data.get("command", "")
    path = input_data.get("path", "")

    if command == "view":
        return editor_view(path, input_data.get("view_range"))
    elif command == "str_replace":
        return editor_str_replace(path, input_data["old_str"], input_data["new_str"])
    elif command == "create":
        return editor_create(path, input_data["file_text"])
    elif command == "insert":
        return editor_insert(path, input_data["insert_line"], input_data["insert_text"])
    else:
        return f"Error: Unknown editor command: {command}"


def editor_view(path, view_range=None):
    path = os.path.expanduser(path)
    print(f"  {DIM}view: {path}{RESET}")
    if not os.path.exists(path):
        return f"The path {path} does not exist. Please provide a valid path."
    if os.path.isdir(path):
        result = f"Here're the files and directories up to 2 levels deep in {path}, excluding hidden items and node_modules:\n"
        for root, dirs, files in os.walk(path):
            depth = root.replace(path, "").count(os.sep)
            if depth >= 2:
                dirs.clear()
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for name in sorted(dirs + files):
                if name.startswith("."):
                    continue
                full = os.path.join(root, name)
                try:
                    size = os.path.getsize(full)
                    if size < 1024:
                        sz = f"{size}B"
                    elif size < 1024 * 1024:
                        sz = f"{size/1024:.1f}K"
                    else:
                        sz = f"{size/(1024*1024):.1f}M"
                except OSError:
                    sz = "?"
                result += f"{sz}\t{full}\n"
        return result
    else:
        try:
            with open(path, "r") as f:
                lines = f.readlines()
        except Exception as e:
            return f"Error reading {path}: {e}"
        if len(lines) > 999999:
            return f"File {path} exceeds maximum line limit of 999,999 lines."
        start, end = 1, len(lines)
        if view_range:
            start = view_range[0]
            end = len(lines) if view_range[1] == -1 else view_range[1]
        result = f"Here's the content of {path} with line numbers:\n"
        for i in range(start - 1, min(end, len(lines))):
            result += f"{i+1:>6}\t{lines[i]}"
        return result


def editor_str_replace(path, old_str, new_str):
    path = os.path.expanduser(path)
    print(f"  {DIM}str_replace in: {path}{RESET}")
    if not os.path.isfile(path):
        return f"Error: The path {path} does not exist. Please provide a valid path."
    with open(path, "r") as f:
        content = f.read()
    count = content.count(old_str)
    if count == 0:
        return f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {path}."
    if count > 1:
        lines = content.split("\n")
        match_lines = [str(i+1) for i, l in enumerate(lines) if old_str in l]
        return f"No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines: {', '.join(match_lines)}. Please ensure it is unique"
    new_content = content.replace(old_str, new_str, 1)
    with open(path, "w") as f:
        f.write(new_content)
    return "Successfully replaced text at exactly one location."


def editor_create(path, file_text):
    path = os.path.expanduser(path)
    print(f"  {DIM}create: {path}{RESET}")
    if os.path.exists(path):
        return f"Error: File {path} already exists"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(file_text)
    return f"File created successfully at: {path}"


def editor_insert(path, insert_line, insert_text):
    path = os.path.expanduser(path)
    print(f"  {DIM}insert at line {insert_line}: {path}{RESET}")
    if not os.path.isfile(path):
        return f"Error: The path {path} does not exist"
    with open(path, "r") as f:
        lines = f.readlines()
    if insert_line < 0 or insert_line > len(lines):
        return f"Error: Invalid `insert_line` parameter: {insert_line}. It should be within the range of lines of the file: [0, {len(lines)}]"
    new_lines = insert_text.split("\n")
    for i, nl in enumerate(new_lines):
        lines.insert(insert_line + i, nl + "\n")
    with open(path, "w") as f:
        f.writelines(lines)
    return f"The file {path} has been edited."

# ─── Memory Tool Executor ────────────────────────────────────────────

def exec_memory(input_data):
    command = input_data.get("command", "")
    path = input_data.get("path", "")

    # Map /memories to our local .memories directory
    def resolve(p):
        if p.startswith("/memories"):
            p = p.replace("/memories", MEMORY_DIR, 1)
        resolved = str(Path(p).resolve())
        mem_resolved = str(Path(MEMORY_DIR).resolve())
        if not resolved.startswith(mem_resolved):
            return None
        return resolved

    real_path = resolve(path) if path else None

    if command == "view":
        if real_path is None:
            return f"The path {path} does not exist. Please provide a valid path."
        # Reuse the editor view with /memories mapped
        return editor_view(real_path, input_data.get("view_range"))

    elif command == "create":
        if real_path is None:
            return f"Error: Invalid path {path}"
        print(f"  {DIM}memory create: {path}{RESET}")
        os.makedirs(os.path.dirname(real_path) or ".", exist_ok=True)
        if os.path.exists(real_path):
            return f"Error: File {path} already exists"
        with open(real_path, "w") as f:
            f.write(input_data.get("file_text", ""))
        return f"File created successfully at: {path}"

    elif command == "str_replace":
        if real_path is None:
            return f"Error: The path {path} does not exist. Please provide a valid path."
        print(f"  {DIM}memory str_replace: {path}{RESET}")
        return editor_str_replace(real_path, input_data["old_str"], input_data["new_str"])

    elif command == "insert":
        if real_path is None:
            return f"Error: The path {path} does not exist"
        print(f"  {DIM}memory insert: {path}{RESET}")
        return editor_insert(real_path, input_data["insert_line"], input_data["insert_text"])

    elif command == "delete":
        if real_path is None:
            return f"Error: The path {path} does not exist"
        print(f"  {DIM}memory delete: {path}{RESET}")
        if not os.path.exists(real_path):
            return f"Error: The path {path} does not exist"
        if os.path.isdir(real_path):
            shutil.rmtree(real_path)
        else:
            os.unlink(real_path)
        return f"Successfully deleted {path}"

    elif command == "rename":
        old_path = resolve(input_data.get("old_path", ""))
        new_path = resolve(input_data.get("new_path", ""))
        if old_path is None or not os.path.exists(old_path):
            return f"Error: The path {input_data.get('old_path')} does not exist"
        if new_path is None:
            return f"Error: Invalid destination path"
        if os.path.exists(new_path):
            return f"Error: The destination {input_data.get('new_path')} already exists"
        print(f"  {DIM}memory rename: {input_data.get('old_path')} -> {input_data.get('new_path')}{RESET}")
        os.rename(old_path, new_path)
        return f"Successfully renamed {input_data.get('old_path')} to {input_data.get('new_path')}"

    else:
        return f"Error: Unknown memory command: {command}"

# ─── Spawn Agent Executor ────────────────────────────────────────────

AGENT_DEPTH = int(os.environ.get("INSIDE_OUT_DEPTH", "0"))
MAX_AGENT_DEPTH = 3

def exec_spawn_agent(input_data):
    task = input_data["task"]
    agent_model = input_data.get("model", MODEL)
    depth = AGENT_DEPTH + 1

    if depth > MAX_AGENT_DEPTH:
        return f"Error: Maximum agent depth ({MAX_AGENT_DEPTH}) exceeded. Cannot spawn deeper."

    print(f"  {DIM}spawning sub-agent (depth {depth}, model {agent_model})...{RESET}")
    print(f"  {DIM}task: {task[:100]}{'...' if len(task) > 100 else ''}{RESET}")

    try:
        r = subprocess.run(
            ["python3", os.path.abspath(__file__)],
            input=task + "\n",
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "INSIDE_OUT_MODEL": agent_model, "INSIDE_OUT_DEPTH": str(depth)},
        )
        # Extract just the text output, strip ANSI codes and prompts
        import re
        output = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout)
        # Remove the banner and prompt lines
        lines = output.split("\n")
        result_lines = []
        in_content = False
        for line in lines:
            if line.strip().startswith("you>"):
                in_content = True
                continue
            if in_content and line.strip() not in ("Bye!", ""):
                result_lines.append(line)
        result = "\n".join(result_lines).strip()
        if r.returncode != 0 and r.stderr:
            result += f"\n[agent stderr: {r.stderr[:500]}]"
        return result or "(sub-agent produced no output)"
    except subprocess.TimeoutExpired:
        return "Error: Sub-agent timed out after 300 seconds"

# ─── Tool Router ─────────────────────────────────────────────────────

def execute_tool(name, input_data):
    if name == "bash":
        return exec_bash(input_data)
    elif name == "str_replace_based_edit_tool":
        return exec_editor(input_data)
    elif name == "memory":
        return exec_memory(input_data)
    elif name == "spawn_agent":
        return exec_spawn_agent(input_data)
    else:
        return f"Unknown tool: {name}"

# ─── Ant CLI Interface ──────────────────────────────────────────────

def call_ant(messages):
    cmd = [
        ANT, "--format", "json",
        "beta:messages", "create",
        "--model", MODEL,
        "--max-tokens", str(MAX_TOKENS),
        "--system", json.dumps(SYSTEM_PROMPT),
    ]
    for tool in ALL_TOOLS:
        cmd += ["--tool", json.dumps(tool)]
    for msg in messages:
        cmd += ["--message", json.dumps(msg)]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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
            print(f"\n{RED}Error: {e}{RESET}")
            return

        content = response.get("content", [])
        stop_reason = response.get("stop_reason")

        tool_results = []
        for block in content:
            btype = block.get("type", "")

            if btype == "text" and block.get("text", "").strip():
                print(f"\n{BOLD}{block['text']}{RESET}")

            elif btype == "tool_use":
                # Client-side tool — we execute it
                tool_name = block["name"]
                tool_input = block["input"]
                print(f"\n{YELLOW}[{tool_name}]{RESET}")
                output = execute_tool(tool_name, tool_input)
                display = output if len(output) <= 500 else output[:500] + "..."
                print(f"{GREEN}{display}{RESET}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": output[:30000],
                })

            elif btype == "server_tool_use":
                # Server-side tool — Anthropic is executing it
                print(f"\n{CYAN}[{block['name']}] {json.dumps(block.get('input', {}))}{RESET}")

            elif btype == "web_search_tool_result":
                results = block.get("content", [])
                if isinstance(results, list):
                    for r in results[:5]:
                        if r.get("type") == "web_search_result":
                            age = f" ({r['page_age']})" if r.get("page_age") else ""
                            print(f"  {DIM}{r.get('title', '')}{age}{RESET}")
                            print(f"  {DIM}{r.get('url', '')}{RESET}")
                elif isinstance(results, dict) and results.get("type") == "web_search_tool_result_error":
                    print(f"  {RED}Search error: {results.get('error_code')}{RESET}")

            elif btype == "web_fetch_tool_result":
                rc = block.get("content", {})
                if isinstance(rc, dict):
                    if rc.get("type") == "web_fetch_tool_error":
                        print(f"  {RED}Fetch error: {rc.get('error_code')}{RESET}")
                    else:
                        url = rc.get("url", "")
                        print(f"  {DIM}fetched: {url}{RESET}")

        # If stop reason is end_turn or pause_turn with no client tools needed, we're done
        if stop_reason == "end_turn" or (stop_reason == "pause_turn" and not tool_results):
            # For pause_turn, add assistant content and continue to get server results
            if stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": content})
                continue
            return

        if not tool_results and stop_reason != "pause_turn":
            return

        messages.append({"role": "assistant", "content": content})
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    print(f"\n{DIM}(reached {MAX_ROUNDS} tool rounds, stopping){RESET}")


def main():
    global MODEL

    # Ensure memory directory exists
    os.makedirs(MEMORY_DIR, exist_ok=True)

    print(f"""{BOLD}
 ╔═══════════════════════════════════════════╗
 ║   Inside-Out Claude Code  v2             ║
 ║   Native API tools + persistent memory   ║
 ╚═══════════════════════════════════════════╝{RESET}
 Model: {MODEL}
 Tools: bash, text_editor, memory, web_search, web_fetch
 Commands: quit, clear, model <name>
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
            print(f"{DIM}(conversation cleared, memory preserved){RESET}")
            continue
        if user_input.lower().startswith("model "):
            MODEL = user_input.split(None, 1)[1]
            print(f"{DIM}(switched to {MODEL}){RESET}")
            continue

        messages.append({"role": "user", "content": user_input})
        process_turn(messages)


if __name__ == "__main__":
    main()
