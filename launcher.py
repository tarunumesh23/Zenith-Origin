import subprocess
import sys
import os
from datetime import datetime


def get_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def launch():
    print(f"\n{'='*40}")
    print(f"  ZO Bot Launcher")
    print(f"  {get_time()}")
    print(f"{'='*40}\n")

    bot_path = os.path.join(os.path.dirname(__file__), "bot.py")
    python = sys.executable

    while True:
        print(f"  🚀 Starting bot...\n")
        try:
            process = subprocess.run([python, bot_path])

            if process.returncode == 0:
                print(f"\n  🛑 Bot stopped cleanly. Exiting launcher.")
                break

            print(f"\n  ⚠️  Bot crashed (exit code {process.returncode})")
            print(f"  🔄 Restarting...\n")

        except KeyboardInterrupt:
            print(f"\n  🛑 Launcher stopped by user.")
            break


if __name__ == "__main__":
    launch()