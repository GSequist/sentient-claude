from dotenv import load_dotenv
from utils.files import get_file_list
from cache.state import RedisStateManager
import datetime

load_dotenv()


class Agent:

    def __init__(
        self,
        name,
        instructions_template,
        tools,
        claude_id,
        model="claude-4.5",
        personality=None,
    ):
        self.name = name
        self.instructions_template = instructions_template
        self.claude_id = claude_id
        self.model = model
        self.personality = personality
        self.tools = tools

    def get_claudes_files(self) -> str:
        """retrieve the list of files claude fetched from web, created"""
        try:
            files = get_file_list(self.claude_id)
            if files:
                return "\n".join(files)
            return "No files available."
        except Exception as e:
            print(f"Error listing files from S3: {e}")
            return "Error retrieving file list from S3."

    def get_instructions(self) -> str:
        """get instructions with user-specific context"""
        current_datetime = datetime.datetime.now().strftime("%B %d, %Y")
        user_files = self.get_claudes_files()
        instructions = {
            "user_files": user_files,
            "current_datetime": current_datetime,
            "personality": self.personality or "",
        }
        return self.instructions_template.format(**instructions)
