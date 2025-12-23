from utils.files import ensure_claude_workspace
from cache.state import RedisStateManager
from utils.helpers import KERNEL_PID_DIR
from sandbox.kernel import (
    cleanup_user_kernels,
    get_or_create_persistent_kernel,
)
import asyncio
import os
import re


def extract_filenames_from_code(code: str) -> list[str]:
    patterns = [
        r'\.read[_a-z]*\([\'"]([^/\'"]+\.[a-z0-9]+)[\'"]',
        r'\.load[_a-z]*\([\'"]([^/\'"]+\.[a-z0-9]+)[\'"]',
        r'open\([\'"]([^/\'"]+\.[a-z0-9]+)[\'"]',
        r'imread\([\'"]([^/\'"]+\.[a-z0-9]+)[\'"]',
        r'\.open\([\'"]([^/\'"]+\.[a-z0-9]+)[\'"]',
        r"!(?:cat|head|tail|less|more|file|stat|wc|grep|awk|sed|cut|sort|uniq|nano|vim|vi|emacs|bat|type)\s+([^\s;|&><\'\"]+\.[a-z0-9]+)",
        r'[\'"]([a-zA-Z0-9_\-]+\.[a-z0-9]+)[\'"]',
    ]

    filenames = set()
    for pattern in patterns:
        matches = re.findall(pattern, code, re.IGNORECASE)
        for match in matches:
            if (
                "." in match
                and not match.startswith(".")
                and len(match.split(".")[-1]) <= 10
            ):
                filenames.add(match)

    return sorted(list(filenames))


async def optimistic_kernel_cleanup(redis_state: RedisStateManager):
    """Check for expired kernels and clean them up proactively"""
    try:
        user_ttls = redis_state.get_all_kernel_users_with_ttl()

        # Check which users have local kernels but expired Redis TTL
        if os.path.exists(KERNEL_PID_DIR):
            for user_dir in os.listdir(KERNEL_PID_DIR):
                user_id = user_dir
                ttl = user_ttls.get(user_id, -2)  # -2 means expired/not exists

                # If TTL is expired/not exists (-2) or about to expire (< 10 seconds)
                if ttl == -2 or (ttl > 0 and ttl < 10):
                    try:
                        cleanup_user_kernels(user_id)
                    except Exception as e:
                        print(f"❌ Error cleaning up kernel for {user_id}: {e}")
    except Exception as e:
        print(f"❌ Error in optimistic cleanup: {e}")


async def kernel(code: str, filenames: list[str] = None, *, claude_id: str) -> str:
    """write python code enclosed in triple backtick markdown code blocks. all python code must be valid and executable in a Jupyter Python 3 kernel environment. ALWAYS reference files in code with their filenames only NEVER absolute file paths! Example: Use pd.read_csv('data.csv') NOT pd.read_csv('workspace/user/data.csv') - the kernel runs in your user directory so files are already accessible by filename alone! ALWAYS add print at the end of the code. Print successful execution of the code and the result. If the result of the function is 'status': 'error', explain to user what happened and immediately rewrite the code and relaunch the function. All file operations should use ONLY filenames without paths (e.g., 'data.csv' not '/path/to/data.csv'). CRITICAL: explicitly save any data that needs to persist (e.g., df.to_excel('output.xlsx'), plt.savefig('plot.png')) as objects in memory are lost after execution.
    #parameters:
    code: the python code to execute - remember that you have to send in full executable code.
    filenames: MANDATORY if your code contains file operations like pd.read_csv('file.xlsx'), open('file.txt'), etc. Must be the list of exact filenames your code tries to access. If not provided, files won't be fetched from S3 and code will fail with FileNotFoundError. Send in filenames only not absolute paths!
    """
    max_tokens = 60000
    redis_state = RedisStateManager()

    await optimistic_kernel_cleanup(redis_state)

    if not redis_state.acquire_kernel_lock(claude_id, 30):
        return "Another request is using kernel, try again", "", [], max_tokens

    if any(path in code for path in ["workspace/", "/tmp/"]):
        return (
            f"ERROR: Code contains absolute file paths. Use ONLY filenames (e.g., 'data.csv') NOT absolute paths (e.g., 'workspace/user/data.csv'). The kernel runs in your user directory so files are accessible by filename alone. Please rewrite your code using only filenames.",
            "",
            [],
            max_tokens,
        )

    auto_extracted = extract_filenames_from_code(code)

    if filenames is None:
        filenames = auto_extracted
    elif isinstance(filenames, str):
        if "," in filenames:
            filenames = [f.strip() for f in filenames.split(",")]
        else:
            filenames = [filenames]
    elif not isinstance(filenames, list):
        filenames = [str(filenames)]

    filenames = list(set(filenames + auto_extracted))

    filenames = [os.path.basename(f) for f in filenames]

    ##### files are local no s3 - just verify they exist
    user_workspace = ensure_claude_workspace(claude_id)

    try:
        kc, flusher_task = await get_or_create_persistent_kernel(claude_id)

        await asyncio.to_thread(kc.execute, code)
        results, output, file_list = await flusher_task

        ###extend redis
        redis_state.extend_kernel_ttl(claude_id, 120)
        ###return - now includes file_list in results
        files_info = ""
        if file_list:
            files_info = "\n\nFiles generated:\n" + "\n".join(
                [f"- {f['name']} ({f['type']})" for f in file_list]
            )
        return results + files_info, output, [], max_tokens

    except Exception as e:
        print(f"❌ DEBUG: Exception in kernel execution: {e}")
        return f"kernel failed: {e}", "", [], max_tokens
    finally:
        redis_state.release_kernel_lock(claude_id)
