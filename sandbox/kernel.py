from jupyter_client import BlockingKernelClient
from cache.state import RedisStateManager
from utils.helpers import KERNEL_PID_DIR
from utils.files import ensure_claude_workspace
import subprocess
import asyncio
import pathlib
import signal
import shutil
import base64
import queue
import json
import time
import sys
import re
import os

############################################################################################################


def cleanup_user_kernels(claude_id):
    redis_state = RedisStateManager()
    user_pid_dir = os.path.join(KERNEL_PID_DIR, claude_id)
    kernel_connection_file = os.path.join(
        os.getcwd(), f"kernel_connection_file_{claude_id}.json"
    )

    if os.path.exists(user_pid_dir):
        for pid_file in os.listdir(user_pid_dir):
            pid = int(pid_file.split(".pid")[0])
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception as e:
                print(
                    f"Error while force cleaning up pid {pid} for claude {claude_id}: {str(e)}"
                )
        try:
            shutil.rmtree(user_pid_dir)
        except Exception as e:
            print(f"Error removing PID directory for claude {claude_id}: {str(e)}")
    else:
        pass  # avoid clash with rich Live implement logging if needed

    if os.path.exists(kernel_connection_file):
        try:
            os.remove(kernel_connection_file)
        except Exception as e:
            print(f"Error removing kernel connection file for user {claude_id}: {e}")
    else:
        pass  # avoid clash with rich Live implement logging if needed

    # REDIS FALLBACK: Kill kernel using PID stored in Redis (cross-worker cleanup)
    try:
        redis_pid = redis_state.get_kernel_pid(claude_id)
        if redis_pid:
            try:
                # Check if process is actually running
                os.kill(redis_pid, 0)  # Signal 0 just checks if process exists
                # If we get here, process exists - kill it
                os.kill(redis_pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                print(f"🔍 DEBUG: Process {redis_pid} from Redis already dead")
            finally:
                # Always clean up Redis PID entry
                redis_state.delete_kernel_pid(claude_id)
        else:
            pass  # avoid clash with rich Live implement logging if needed
    except Exception as e:
        print(f"Error in Redis PID cleanup: {e}")


############################################################################################################


def clean_traceback(traceback_list):
    """erase terminal control characters from traceback lines."""
    ansi_escape = re.compile(r"\x1b\[([0-9]+)(;[0-9]+)*m")
    return [ansi_escape.sub("", line) for line in traceback_list]


async def flush_kernel_msgs(kc, claude_id, msg_fetch_timeout=2.0, overall_timeout=30.0):
    task_results = []
    output = None
    error = None
    kernel_done = False
    meaningful_output = False

    # Track files before execution to detect new files
    user_folder = ensure_claude_workspace(claude_id)
    files_before = (
        set(os.listdir(user_folder)) if os.path.exists(user_folder) else set()
    )

    # Track files created during kernel output processing (to avoid duplicates)
    kernel_created_files = set()

    last_message_time = asyncio.get_event_loop().time()
    while True:
        current_time = asyncio.get_event_loop().time()
        if current_time - last_message_time > overall_timeout:
            kernel_done = True
            break
        try:
            msg = kc.get_iopub_msg(timeout=msg_fetch_timeout)
            last_message_time = asyncio.get_event_loop().time()
            msg_type = msg["msg_type"]
            if msg_type == "status":
                execution_state = msg["content"]["execution_state"]
                if execution_state == "idle" and meaningful_output:
                    kernel_done = True
                    break
            elif msg_type == "execute_input":
                code = msg["content"]["code"]
            elif msg_type in ("execute_result", "display_data", "update_display_data"):
                content = ""
                if "text/plain" in msg["content"].get("data", {}):
                    content = msg["content"]["data"]["text/plain"]
                    output = content
                if "text/html" in msg["content"].get("data", {}):
                    content = msg["content"].get("data", {}).get("text/html")
                    output = content
                if "image/png" in msg["content"].get("data", {}):
                    image_data = msg["content"]["data"]["image/png"]
                    output_filename = f"kernel_image{int(time.time())}.png"
                    output_path = os.path.join(user_folder, output_filename)
                    with open(output_path, "wb") as img_file:
                        img_file.write(base64.b64decode(image_data))
                        output = f"/api/files/serve/{output_filename}"
                    kernel_created_files.add(output_filename)  # Track this file
                    content = "image was generated and sent to front end"
                if content:
                    meaningful_output = True
                    task_results.append(content)
            elif msg_type == "stream":
                stream_name = msg["content"]["name"]
                content = msg["content"]["text"]
                output = content
                if content:
                    meaningful_output = True
                    task_results.append(content)
            elif msg_type == "error":
                error_content = msg["content"]
                traceback_lines = clean_traceback(error_content.get("traceback", []))
                error_message = error_content.get("evalue", "Unknown error")
                error = f"{error_message} at:\n" + "\n".join(traceback_lines)
                output = error
                kernel_done = True
                break
        except queue.Empty:
            await asyncio.sleep(0.1)
            continue

        except Exception as e:
            error = f"Exception while getting message from kernel: {e}"
            break

    if not kernel_done and current_time - last_message_time > overall_timeout:
        error = "kernel execution timed out."

    files_after = set(os.listdir(user_folder)) if os.path.exists(user_folder) else set()
    new_files = files_after - files_before

    file_outputs = []
    file_list = []

    for filename in new_files:
        if filename in kernel_created_files:
            file_ext = os.path.splitext(filename)[1].lower()
            file_list.append(
                {
                    "name": filename,
                    "type": file_ext[1:] if file_ext else "unknown",
                    "url": output if output else None,
                }
            )
            continue

        file_path = os.path.join(user_folder, filename)
        file_ext = os.path.splitext(filename)[1].lower()

        if file_ext == ".pdf":
            ##############cant render pdfs
            pdf_url = f"/api/files/serve/{filename}"
            file_list.append({"name": filename, "type": "pdf", "url": pdf_url})
            task_results.append(f"PDF generated: {filename}")

        elif file_ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp"]:
            ########## can render images
            img_url = f"/api/files/serve/{filename}"
            file_outputs.append(img_url)
            file_list.append({"name": filename, "type": "image", "url": img_url})
            task_results.append(f"Image generated: {filename}")

        ########Other files (CSV, TXT, JSON, etc.) - just list them
        else:
            file_list.append(
                {
                    "name": filename,
                    "type": file_ext[1:] if file_ext else "unknown",
                    "path": file_path,
                }
            )

    if file_outputs and not output:
        output = file_outputs[0]  ######show first

    if error:
        return error, error, file_list
    elif task_results or new_files:
        combined_results = "\n".join(task_results)
        if new_files and not task_results:
            combined_results = (
                f"Generated {len(new_files)} file(s): {', '.join(new_files)}"
            )
        return combined_results, output, file_list
    else:
        return (
            f"""code executed successfully, but produced no explicit output.
            This could be because the code execution was without an outcome.
            Check user workspace for any generated files as a result of the code.
            Remember the code you sent in was {code}""",
            "Code executed successfully",
            [],
        )


############################################################################################################


async def start_kernel(claude_id, msg_fetch_timeout=2.0):
    redis_state = RedisStateManager()
    workspace = ensure_claude_workspace(claude_id)
    kernel_connection_file = os.path.join(
        os.getcwd(), f"kernel_connection_file_{claude_id}.json"
    )
    if os.path.isfile(kernel_connection_file):
        os.remove(kernel_connection_file)
    launch_kernel_script_path = os.path.join(
        pathlib.Path(__file__).parent.resolve(), "launch_kernel.py"
    )
    user_pid_dir = os.path.join(KERNEL_PID_DIR, claude_id)
    os.makedirs(user_pid_dir, exist_ok=True)
    kernel_process = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            launch_kernel_script_path,
            "--IPKernelApp.connection_file",
            kernel_connection_file,
            "--matplotlib=inline",
            "--quiet",
        ],
        cwd=workspace,
    )

    with open(os.path.join(user_pid_dir, f"{kernel_process.pid}.pid"), "w") as p:
        p.write("kernel")

    redis_state.set_kernel_pid(claude_id, kernel_process.pid)
    while not await asyncio.to_thread(os.path.isfile, kernel_connection_file):
        await asyncio.sleep(0.1)
    with open(kernel_connection_file, "r") as fp:
        json.load(fp)
    kc = BlockingKernelClient(connection_file=kernel_connection_file)
    kc.load_connection_file()
    kc.start_channels()
    kc.wait_for_ready()

    flusher_task = asyncio.create_task(
        flush_kernel_msgs(kc, claude_id, msg_fetch_timeout)
    )

    return kc, flusher_task


############################################################################################################end


async def get_or_create_persistent_kernel(claude_id: str):

    user_pid_dir = os.path.join(KERNEL_PID_DIR, claude_id)
    kernel_connection_file = os.path.join(
        os.getcwd(), f"kernel_connection_file_{claude_id}.json"
    )

    if os.path.exists(user_pid_dir) and os.path.exists(kernel_connection_file):
        try:
            ######### check if kernel PROCESS is alive instead of connection
            kernel_alive = False
            for pid_file in os.listdir(user_pid_dir):
                if pid_file.endswith(".pid"):
                    pid = int(pid_file.split(".pid")[0])
                    try:
                        ########### process exists and is running
                        os.kill(pid, 0)  # Signal 0 just checks if process exists
                        kernel_alive = True
                        break
                    except (OSError, ProcessLookupError):
                        ############# process doesn't exist, clean up stale PID file
                        os.remove(os.path.join(user_pid_dir, pid_file))

            if kernel_alive:
                kc = BlockingKernelClient(connection_file=kernel_connection_file)
                kc.load_connection_file()
                kc.start_channels()

                ensure_claude_workspace(claude_id)

                flusher_task = asyncio.create_task(
                    flush_kernel_msgs(kc, claude_id, 2.0)
                )
                return kc, flusher_task
            else:
                pass  # avoid clash with rich Live implement logging if needed

        except Exception as e:
            print(f"❌ Exception during reconnect: {e}")

    ######### create new kernel if none exists locally
    cleanup_user_kernels(claude_id)
    return await start_kernel(claude_id)
