import subprocess
import sys
import os
import re
from datetime import datetime


def get_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Log filtering ─────────────────────────────────────────────────────────────

# Patterns to suppress entirely (noisy but harmless)
_SUPPRESS_PATTERNS = [
    re.compile(r"Warning: Table '.+' already exists"),
    re.compile(r"await self\._query\(query\)"),
    re.compile(r"await self\._read_query_result"),
    re.compile(r"await result\.read\(\)"),
    re.compile(r"await conn\.query\(q\)"),
    re.compile(r"first_packet = await"),
    re.compile(r"packet\.raise_for_error\(\)"),
    re.compile(r"err\.raise_mysql_exception"),
    re.compile(r"raise errorclass\(errno"),
    re.compile(r"File \"/app/\.venv"),
    re.compile(r"^\s*$"),  # blank lines
]

# Patterns that indicate a cog loaded successfully
_COG_LOADED   = re.compile(r"Cog\s+»\s+Loaded\s+(.+)")
_COG_FAILED   = re.compile(r"Cog\s+»\s+Failed\s+(.+)")
_COG_SKIP     = re.compile(r"Cog\s+»\s+Skipped\s+(.+)")
_COG_DUP      = re.compile(r"Cog\s+»\s+Duplicate\s+(.+)")
_COG_LOADED2  = re.compile(r"Cog loaded\s+»\s+(.+)")  # AdminSpiritRoots style

# Patterns for the error reason that follows a Failed cog
_EXTENSION_ERR = re.compile(
    r"ExtensionFailed: Extension '(.+?)' raised an error: (.+)"
)
_IMPORT_ERR   = re.compile(r"ImportError: (.+)")
_CLIENT_ERR   = re.compile(r"ClientException: (.+)")
_CMD_REG_ERR  = re.compile(r"CommandRegistrationError: (.+)")


def _should_suppress(line: str) -> bool:
    return any(p.search(line) for p in _SUPPRESS_PATTERNS)


def _format_line(line: str, pending_failures: dict) -> str | None:
    """
    Returns a formatted line to print, or None to suppress.
    Also populates pending_failures {ext_name: reason} for summary.
    """
    if _should_suppress(line):
        return None

    # Cog loaded (normal)
    m = _COG_LOADED.search(line) or _COG_LOADED2.search(line)
    if m:
        return f"  ✅  {m.group(1).strip()}"

    # Cog failed
    m = _COG_FAILED.search(line)
    if m:
        ext = m.group(1).strip()
        pending_failures["__last__"] = ext
        return f"  ❌  {ext}"

    # Cog skipped / duplicate
    m = _COG_SKIP.search(line)
    if m:
        return f"  ⏭️   {m.group(1).strip()} (skipped)"

    m = _COG_DUP.search(line)
    if m:
        return f"  ⚠️   {m.group(1).strip()} (duplicate — skipped)"

    # Extension error — capture the reason
    m = _EXTENSION_ERR.search(line)
    if m:
        ext, reason = m.group(1), m.group(2)
        pending_failures[ext] = reason
        return f"       ↳ {reason}"

    # Bare ImportError / ClientException / CommandRegistrationError
    for pattern in (_IMPORT_ERR, _CLIENT_ERR, _CMD_REG_ERR):
        m = pattern.search(line)
        if m:
            return f"       ↳ {m.group(1)}"

    # DB connected
    if "Database" in line and "Connected" in line:
        return f"  🗄️   Database connected"

    if "Database" in line and "Failed" in line:
        return f"  🗄️   ❌ Database FAILED to connect"

    # Migration summary
    if "Migrations" in line and "statement" in line:
        return f"  🔧  {line.split('INFO')[-1].strip()}"

    if "Migration" in line and ("OK" in line or "SKIP" in line):
        return None  # suppress per-migration noise unless it's a failure

    if "Migration" in line and "FAILED" in line:
        return f"  🔧  ❌ {line.split('ERROR')[-1].strip()}"

    # Slash cmds synced
    if "Slash cmds" in line and "Synced" in line:
        return f"  🔗  Slash commands synced"

    # Cog summary line
    if "Cogs" in line and "loaded" in line:
        return f"  📦  {line.split('INFO')[-1].strip()}"

    # Logged in
    if "Logged in as" in line:
        name = line.split("Logged in as")[-1].strip()
        return f"\n  🤖  {name}\n"

    # Separator lines from bot
    if "=" * 10 in line:
        return None

    # Generic INFO — pass through trimmed
    if "INFO" in line:
        msg = line.split("INFO")[-1].strip()
        if msg and msg != "—":
            return f"  ℹ️   {msg}"
        return None

    # ERROR lines not already caught
    if "ERROR" in line:
        msg = line.split("ERROR")[-1].strip()
        if msg:
            return f"  🔴  {msg}"
        return None

    # Tracebacks and file paths — suppress
    if line.strip().startswith(("File ", "Traceback", "The above", "raise ", "await ")):
        return None

    return None  # suppress anything else not explicitly handled


# ── Launcher ──────────────────────────────────────────────────────────────────

def launch():
    print(f"\n{'═'*48}")
    print(f"  ZO Bot Launcher")
    print(f"  {get_time()}")
    print(f"{'═'*48}\n")

    bot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")
    python   = sys.executable

    run_count = 0

    while True:
        run_count += 1
        if run_count > 1:
            print(f"\n{'─'*48}")
            print(f"  🔄  Restart #{run_count}  —  {get_time()}")
            print(f"{'─'*48}\n")
        else:
            print(f"  🚀  Starting bot...\n")

        pending_failures: dict[str, str] = {}

        try:
            process = subprocess.Popen(
                [python, "-u", bot_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for raw_line in process.stdout:
                line = raw_line.rstrip()
                formatted = _format_line(line, pending_failures)
                if formatted is not None:
                    print(formatted)

            process.wait()
            exit_code = process.returncode

        except KeyboardInterrupt:
            print(f"\n  🛑  Launcher stopped by user.")
            break

        # ── Exit summary ──────────────────────────────────────────────
        print(f"\n{'─'*48}")
        if exit_code == 0:
            print(f"  🛑  Bot stopped cleanly at {get_time()}.")
            print(f"{'─'*48}\n")
            break
        else:
            print(f"  ⚠️   Bot exited with code {exit_code} at {get_time()}")
            if pending_failures:
                non_meta = {k: v for k, v in pending_failures.items() if k != "__last__"}
                if non_meta:
                    print(f"\n  Failed cogs summary:")
                    for ext, reason in non_meta.items():
                        short = ext.split(".")[-1]
                        print(f"    ❌  {short:<30} {reason}")
            print(f"\n  🔄  Restarting...\n")


if __name__ == "__main__":
    launch()