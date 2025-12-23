from utils.helpers import WORK_FOLDER
from dotenv import load_dotenv
from typing import List, Optional
import os


load_dotenv()


def ensure_claude_workspace(claude_id: str) -> str:
    """Ensure claude has a workspace directory"""
    user_folder = os.path.join(WORK_FOLDER, claude_id)
    os.makedirs(user_folder, exist_ok=True)
    return user_folder


def get_file_(claude_id: str, filename: str) -> Optional[str]:
    """Get file from workspace"""
    user_folder = ensure_claude_workspace(claude_id)
    local_file_path = os.path.join(user_folder, filename)
    if local_file_path is None:
        return None
    return local_file_path


def cleanup_local_file(file_path: str) -> bool:
    """Remove local file after use"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False
    except Exception as e:
        print(f"Error cleaning up file {file_path}: {e}")
        return False


def delete_file_(claude_id: str, filename: str) -> bool:
    """Delete a file"""
    claude_workspace = ensure_claude_workspace(claude_id)
    local_file_path = os.path.join(claude_workspace, filename)
    try:
        os.remove(local_file_path)
        return True
    except Exception as e:
        print(f"Error {e}")
        return False


def get_file_list(claude_id: str) -> List[str]:
    """Get list of files"""
    claude_workspace = ensure_claude_workspace(claude_id)
    all_items = os.listdir(claude_workspace)
    files = [
        item
        for item in all_items
        if os.path.isfile(os.path.join(claude_workspace, item))
    ]
    return files
