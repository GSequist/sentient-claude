from PIL import Image
import unicodedata
import tiktoken
import base64
import os
import io

############################################################################################################
##tokenizer

tokenizer = tiktoken.get_encoding("cl100k_base")

############################################################################################################
##dirs

WORK_FOLDER = os.path.join(os.getcwd(), "workspace/")
KERNEL_PID_DIR = os.path.join(os.getcwd(), "process_pids")

############################################################################################################


def sanitize_and_encode_image_(img_data):
    try:
        if isinstance(img_data, str) and os.path.exists(img_data):
            with Image.open(img_data) as img:
                img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG")
                return base64.b64encode(buffer.getvalue()).decode("utf-8")
        else:
            with Image.open(io.BytesIO(img_data)) as img:
                img = img.convert("RGB")
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG")
                return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"Image encoding error: {e}")
        return None


def normalize_filename(filename: str) -> str:
    """
    Normalize filename to NFC form to avoid Unicode encoding issues.

    macOS uses NFD (decomposed), Linux/Windows use NFC (composed).
    This ensures consistent filenames across systems.
    """
    return unicodedata.normalize("NFC", filename)


#########################################


def check_and_setup_env():
    """Check for required environment variables and prompt for missing ones"""
    required_vars = {
        "ANTHROPIC_API_KEY": "Anthropic API Key (from console.anthropic.com)",
        "SERPAPI_KEY": "SerpAPI Key (from serpapi.com for web search)",
    }

    missing_vars = []

    for var_name, description in required_vars.items():
        value = os.getenv(var_name)
        if not value or value.strip() == "":
            missing_vars.append((var_name, description))

    if missing_vars:
        print("⚠️  Missing Required Environment Variables[/bold red]\n")

    return True


#########################################
