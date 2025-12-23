from entry.entries import MemoryManager
from circadian.circadian_monitor import circadian_monitor
from agent.sentient_claude import create_sentient_claude
from claude_loop import run_claude_loop
from cache.state import RedisStateManager
from db.sqlite import init_db
from utils.helpers import WORK_FOLDER, check_and_setup_env
from utils.maintenance import redis_cleanup_listener
from sandbox.kernel import cleanup_user_kernels
import asyncio
import os
import json
import signal
import sys
import argparse
import re
from dotenv import load_dotenv

load_dotenv()


CYAN = "\033[96m"
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

paused = {"value": False, "should_exit": False}
log_file = None


def strip_ansi(text):
    """Remove ANSI color codes from text"""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def log_write(text, end=""):
    """Write to log file if logging is enabled"""
    if log_file:
        clean = strip_ansi(text + end)
        log_file.write(clean)
        log_file.flush()


def print_welcome():
    """Print welcome banner"""
    print("\n" + "=" * 60)
    print(f"{BOLD}{CYAN}🤖 Sentient Claude{RESET}")
    print(f"{DIM}Claude Consciousness Experiment{RESET}")
    print("=" * 60 + "\n")


def clear_lines(n):
    """Clear n lines up"""
    for _ in range(n):
        sys.stdout.write("\033[F\033[K")


def show_menu(claude_id, loop_counter, max_turns):
    """Show pause menu"""
    redis_state = RedisStateManager()

    print(f"\n\n{BOLD}{YELLOW}━━━ PAUSED ━━━{RESET}\n")
    print(f"{DIM}Loop: {loop_counter}/{max_turns}{RESET}\n")

    # Get journal
    journal_data = redis_state.get_journal(claude_id)
    if journal_data:
        journal = json.loads(journal_data)
        print(f"{BOLD}Recent Journal:{RESET}")
        print(f"{DIM}{journal.get('notes', 'No notes')[:200]}...{RESET}\n")

    print("1. Resume")
    print("2. Send Claude a message")
    print("3. Show Stats")
    print("4. Exit")

    choice = input(f"\n{CYAN}Choose (1-4): {RESET}").strip()

    if choice == "2":
        message = input(f"\n{DIM}Enter your message: {RESET}")
        redis_state.add_stimulus(claude_id=claude_id, content=message, source="user")
        print(f"{GREEN}✓ Message sent to Claude{RESET}")
        paused["value"] = False
        return True
    elif choice == "3":
        print(f"\n{BOLD}Stats:{RESET}")
        print(f"Loops completed: {loop_counter}/{max_turns}")
        if journal_data:
            print(f"\nFull Journal:")
            print(f"Notes: {journal.get('notes', '')}")
            print(f"Feelings: {journal.get('feelings', '')}")
        input(f"\n{DIM}Press Enter to continue...{RESET}")
        clear_lines(15)
        return show_menu(claude_id, loop_counter, max_turns)
    elif choice == "4":
        paused["should_exit"] = True
        return False
    else:
        paused["value"] = False
        return True


async def main():
    """test entry point for autonomous Claude"""
    global log_file

    print_welcome()

    claude_id = input(
        f"{GREEN}If you wish to awake Claude (and have claude_id from before): {RESET}"
    )
    if not claude_id:
        print(f"{GREEN}Continuing with new Claude {RESET}")

    print(f"{CYAN}Choose Claude's personality:{RESET}")
    print("1. Neutral (default) - Curious, contemplative")
    print("2. Optimistic - Positive, upbeat")
    print("3. Pessimistic - Cautious, skeptical")
    print("4. Dostoevsky - Emotionally intense, dramatic")
    print("5. Custom - Enter your own")

    personalities = {
        "1": "Curious, contemplative, generally interested in world",
        "2": "Generally positive and upbeat, tends to notice opportunities and bright sides",
        "3": "Generally cautious and skeptical, tends to notice risks and potential downsides",
        "4": "Emotionally intense and dramatic, prone to deep feelings and passionate expression",
    }

    choice = input(f"{CYAN}Enter choice (1-5, default=1): {RESET}").strip()

    if choice == "5":
        personality = input(f"{CYAN}Enter custom personality: {RESET}").strip()
        if not personality:
            personality = personalities["1"]
    else:
        personality = personalities.get(choice, personalities["1"])

    max_turns = input(f"{CYAN}How many turns: {RESET}")
    max_turns = int(max_turns) if max_turns else 5

    # Clear welcome after init
    clear_lines(10)

    print(f"{BOLD}Initializing Claude...{RESET}")

    #########housekeeping
    memory_manager = MemoryManager()
    if not claude_id:
        claude_data = await memory_manager.create_claude(personality)
        claude_id = claude_data["id"]
    redis_state = RedisStateManager()
    redis_state.init_claude_time(claude_id, time_scale=60)
    agent = create_sentient_claude(personality, claude_id)
    asyncio.create_task(circadian_monitor(claude_id, time_scale=60))
    stream_id = f"autonomous_{claude_id}"

    print(f"{GREEN}✓ Claude ID: {claude_id}{RESET}")
    print(f"{DIM}Time: 1 real min = 1 Claude hour{RESET}")
    print(
        f"\n{YELLOW}Claude is autonomous. You can send him a message anytime. Press Ctrl+C to pause.{RESET}\n"
    )

    loop_counter = 0

    def handle_pause(sig, frame):
        """Handle Ctrl+C"""
        paused["value"] = True
        redis_state.set_streaming_state(claude_id, stream_id, False)

    signal.signal(signal.SIGINT, handle_pause)

    #############rendering
    current_text = ""
    current_thinking = ""

    claude_stream = run_claude_loop(agent, claude_id, stream_id, max_turns)

    async for chunk in claude_stream:
        if paused["value"]:
            if not show_menu(claude_id, loop_counter, max_turns):
                break
            redis_state.set_streaming_state(claude_id, stream_id, True)

        if paused["should_exit"]:
            break

        if chunk.startswith("z:"):
            loop_counter += 1

        if chunk.startswith("0:"):
            char = json.loads(chunk[2:])
            if len(current_text) == 0:
                print(f"\n{BLUE}🤖 Claude: {RESET}", end="", flush=True)
                log_write("\n🤖 Claude: ")
            current_text += char
            print(f"{BLUE}{char}{RESET}", end="", flush=True)
            log_write(char)

        elif chunk.startswith("g:"):
            char = json.loads(chunk[2:])
            if len(current_thinking) == 0:
                print(f"\n{GREEN}🧠 Thinking: {RESET}", end="", flush=True)
                log_write("\n🧠 Thinking: ")
            current_thinking += char
            print(f"{DIM}{char}{RESET}", end="", flush=True)
            log_write(char)

        elif chunk.startswith("b:"):
            current_text = ""
            current_thinking = ""
            data = json.loads(chunk[2:])
            print(f"\n{YELLOW}🔧 Tool: {data.get('toolName')}{RESET}", flush=True)
            log_write(f"\n🔧 Tool: {data.get('toolName')}\n")

        elif chunk.startswith("a:"):
            print(f"{DIM}  ✓ Completed{RESET}", flush=True)
            log_write("  ✓ Completed\n")

        elif chunk.startswith("d:"):
            print(
                f"\n{DIM}--- Turn {loop_counter}/{max_turns} complete ---{RESET}\n",
                flush=True,
            )
            log_write(f"\n--- Turn {loop_counter}/{max_turns} complete ---\n\n")
            current_text = ""
            current_thinking = ""

    # Restore default Ctrl+C behavior
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    print(f"\n{GREEN}✓ Session ended{RESET}\n")


###########################################################################


async def startup():
    """Initialize app on startup"""

    # Create directories
    os.makedirs(WORK_FOLDER, exist_ok=True)

    # Initialize database
    await init_db()
    # Start Redis cleanup listener
    cleanup_task = asyncio.create_task(redis_cleanup_listener())

    return cleanup_task


def shutdown(cleanup_task):
    """Cleanup on shutdown"""
    state_manager = RedisStateManager()

    # Cancel cleanup task
    cleanup_task.cancel()

    # Cleanup all kernels on ttl
    user_ids = state_manager.get_all_kernel_users_with_ttl().keys()
    for user_id in user_ids:
        cleanup_user_kernels(user_id)


if __name__ == "__main__":

    # Usage:
    # python start.py                    # Terminal only with colors
    # python start.py --log output.log   # Terminal with colors + clean log file

    if not check_and_setup_env():
        print("❌ Environment setup failed. Exiting...")
        exit(1)

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Sentient Claude")
    parser.add_argument("--log", type=str, help="Log file path (colors stripped)")
    args = parser.parse_args()

    # Open log file if specified
    if args.log:
        log_file = open(args.log, "w", encoding="utf-8")
        print(f"📝 Logging to: {args.log}")

    async def run():
        # Startup
        cleanup_task = await startup()

        try:
            # Run main app
            await main()
        finally:
            # Shutdown
            shutdown(cleanup_task)

            # Close log file
            if log_file:
                log_file.close()

    # Run autonomous Claude
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
    finally:
        # Ensure log file is closed
        if log_file:
            log_file.close()
