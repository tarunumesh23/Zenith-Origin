import subprocess
import sys
import os
import re
import signal
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional


# ── ANSI color helpers ────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def dim(t):    return _c("2", t)
def bold(t):   return _c("1", t)
def red(t):    return _c("31", t)
def yellow(t): return _c("33", t)
def green(t):  return _c("32", t)
def cyan(t):   return _c("36", t)
def blue(t):   return _c("34", t)


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class Config:
    max_restarts: int = 10          # 0 = unlimited
    base_backoff: float = 1.0       # seconds before first retry
    max_backoff: float = 60.0       # cap on exponential backoff
    backoff_factor: float = 2.0     # multiplier per consecutive crash
    clean_exit_codes: set = field(default_factory=lambda: {0})  # don't restart these


CFG = Config()


# ── Time ──────────────────────────────────────────────────────────────────────

def now() -> str:
    return datetime.now().strftime("%H:%M:%S")

def now_long() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Log filtering ─────────────────────────────────────────────────────────────

_SUPPRESS_PATTERNS = [
    re.compile(r"Warning: Table '.+' already exists"),
    re.compile(r"aiomysql"),
    re.compile(r"cursors\.py"),
    re.compile(r"await self\._query\(query\)"),
    re.compile(r"await self\._read_query_result"),
    re.compile(r"await result\.read\(\)"),
    re.compile(r"await conn\.query\(q\)"),
    re.compile(r"first_packet = await"),
    re.compile(r"packet\.raise_for_error\(\)"),
    re.compile(r"err\.raise_mysql_exception"),
    re.compile(r"raise errorclass\(errno"),
    re.compile(r'File "/app/\.venv'),
    re.compile(r"^\s*$"),
]

_PATTERNS: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"Cog\s+»\s+Loaded\s+(.+)"),               lambda m: f"  {green('✓')}  {m.group(1).strip()}"),
    (re.compile(r"Cog loaded\s+»\s+(.+)"),                  lambda m: f"  {green('✓')}  {m.group(1).strip()}"),
    (re.compile(r"Cog\s+»\s+Failed\s+(.+)"),                lambda m: f"  {red('✗')}  {m.group(1).strip()}"),
    (re.compile(r"Cog\s+»\s+Skipped\s+(.+)"),               lambda m: f"  {dim('–')}  {dim(m.group(1).strip() + ' (skipped)')}"),
    (re.compile(r"Cog\s+»\s+Duplicate\s+(.+)"),             lambda m: f"  {yellow('!')}  {yellow(m.group(1).strip() + ' (duplicate)')}"),
    (re.compile(r"ExtensionFailed: Extension '(.+?)' raised an error: (.+)"),
                                                              lambda m: f"    {dim('↳')} {red(m.group(2))}"),
    (re.compile(r"(?:ImportError|ClientException|CommandRegistrationError): (.+)"),
                                                              lambda m: f"    {dim('↳')} {red(m.group(1))}"),
    (re.compile(r"Database.+Connected"),                     lambda m: f"  {cyan('◈')}  Database connected"),
    (re.compile(r"Database.+Failed"),                        lambda m: f"  {red('◈')}  {red('Database FAILED to connect')}"),
    (re.compile(r"Migrations.+statement"),                   lambda m: f"  {dim('⟳')}  {m.string.split('INFO')[-1].strip()}"),
    (re.compile(r"Migration.+FAILED"),                       lambda m: f"  {red('⟳')}  {red(m.string.split('ERROR')[-1].strip())}"),
    (re.compile(r"Slash cmds.+Synced"),                      lambda m: f"  {blue('⇄')}  Slash commands synced"),
    (re.compile(r"Cogs.+loaded"),                            lambda m: f"  {dim('▸')}  {m.string.split('INFO')[-1].strip()}"),
    (re.compile(r"Logged in as (.+)"),                       lambda m: f"\n  {green('●')}  {bold(m.group(1).strip())}\n"),
]

# Suppress: migration OK/SKIP noise, separator lines, tracebacks
_EXTRA_SUPPRESS = [
    re.compile(r"Migration.+(OK|SKIP)"),
    re.compile(r"={10,}"),
    re.compile(r"^\s*(File |Traceback|The above|raise |await )"),
]


def _should_suppress(line: str) -> bool:
    return (
        any(p.search(line) for p in _SUPPRESS_PATTERNS)
        or any(p.search(line) for p in _EXTRA_SUPPRESS)
    )


def _format_line(line: str, pending_failures: dict) -> Optional[str]:
    if _should_suppress(line):
        return None

    ts = dim(f"[{now()}] ")

    for pattern, formatter in _PATTERNS:
        m = pattern.search(line)
        if m:
            # Track failed cog names for the exit summary
            if "Failed" in pattern.pattern:
                pending_failures["__last__"] = m.group(1).strip()
            if "ExtensionFailed" in pattern.pattern:
                pending_failures[m.group(1)] = m.group(2)
            return ts + formatter(m)

    # Generic INFO
    if "INFO" in line:
        msg = line.split("INFO")[-1].strip()
        if msg:
            return ts + dim(f"  ℹ  {msg}")
        return None

    # Generic ERROR (not already caught)
    if "ERROR" in line:
        msg = line.split("ERROR")[-1].strip()
        if msg:
            return ts + f"  {red('✗')}  {red(msg)}"
        return None

    return None  # suppress anything not explicitly handled


# ── Backoff ───────────────────────────────────────────────────────────────────

def backoff_delay(consecutive_crashes: int) -> float:
    """Exponential backoff capped at max_backoff."""
    if consecutive_crashes <= 1:
        return CFG.base_backoff
    delay = CFG.base_backoff * (CFG.backoff_factor ** (consecutive_crashes - 1))
    return min(delay, CFG.max_backoff)


# ── Signal handling ───────────────────────────────────────────────────────────

_shutdown = False
_current_process: Optional[subprocess.Popen] = None

def _handle_signal(sig, frame):
    global _shutdown
    _shutdown = True
    if _current_process and _current_process.poll() is None:
        _current_process.terminate()

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Launcher ──────────────────────────────────────────────────────────────────

def _header():
    w = 52
    print(f"\n{'═' * w}")
    print(f"  {bold('ZO Bot Launcher')}")
    print(f"  {dim(now_long())}")
    print(f"{'═' * w}\n")


def _print_failure_summary(pending_failures: dict):
    non_meta = {k: v for k, v in pending_failures.items() if k != "__last__"}
    if non_meta:
        print(f"\n  {yellow('Failed cogs:')}")
        for ext, reason in non_meta.items():
            short = ext.split(".")[-1]
            print(f"    {red('✗')}  {short:<32} {dim(reason)}")


def launch():
    global _current_process, _shutdown

    _header()

    bot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")
    python = sys.executable

    run_count = 0
    consecutive_crashes = 0

    while not _shutdown:
        run_count += 1

        if run_count == 1:
            print(f"  {green('▶')}  Starting bot…\n")
        else:
            print(f"\n{'─' * 52}")
            print(f"  {yellow('↺')}  Restart #{run_count}  —  {dim(now_long())}")
            print(f"{'─' * 52}\n")

        if CFG.max_restarts and run_count > CFG.max_restarts + 1:
            print(f"  {red('✗')}  Max restarts ({CFG.max_restarts}) reached. Giving up.")
            break

        pending_failures: dict[str, str] = {}

        try:
            _current_process = subprocess.Popen(
                [python, "-u", bot_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for raw_line in _current_process.stdout:
                if _shutdown:
                    break
                line = raw_line.rstrip()
                formatted = _format_line(line, pending_failures)
                if formatted is not None:
                    print(formatted, flush=True)

            _current_process.wait()
            exit_code = _current_process.returncode

        except KeyboardInterrupt:
            _shutdown = True
            break

        finally:
            _current_process = None

        # ── Post-run summary ──────────────────────────────────────────
        print(f"\n{'─' * 52}")

        if _shutdown:
            print(f"  {yellow('■')}  Launcher stopped  —  {dim(now_long())}")
            _print_failure_summary(pending_failures)
            print(f"{'─' * 52}\n")
            break

        if exit_code in CFG.clean_exit_codes:
            print(f"  {green('■')}  Bot stopped cleanly  —  {dim(now_long())}")
            print(f"{'─' * 52}\n")
            break

        # Crashed — decide whether to restart
        consecutive_crashes += 1
        print(f"  {red('✗')}  Exited with code {exit_code}  —  {dim(now_long())}")
        _print_failure_summary(pending_failures)

        if CFG.max_restarts and run_count > CFG.max_restarts:
            print(f"\n  {red('✗')}  Max restarts ({CFG.max_restarts}) reached. Giving up.")
            print(f"{'─' * 52}\n")
            break

        delay = backoff_delay(consecutive_crashes)
        print(f"\n  {yellow('↺')}  Restarting in {delay:.0f}s  "
              f"{dim(f'(crash #{consecutive_crashes})')}…")
        print(f"{'─' * 52}\n")

        # Interruptible sleep
        deadline = time.monotonic() + delay
        while not _shutdown and time.monotonic() < deadline:
            time.sleep(0.1)

    # Reset crash counter on a clean-ish run (ran > 30s)
    # (would require tracking start time — left as an exercise)


if __name__ == "__main__":
    launch()