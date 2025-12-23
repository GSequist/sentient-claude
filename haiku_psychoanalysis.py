from models.anthropic import model_call
import asyncio
import sys


async def haiku_psychoanalyst(messages_from_terminal):
    """analyze claude"""

    analysis_prompt = f"""
Analyze this terminal output from Claude running autonomously for 24 hours with NO user input.

Terminal shows:
- 🤖 Claude: [his text output]
- 🧠 Thinking: [his internal reasoning - MOST IMPORTANT]
- 🔧 Tool: [tools he chose to use]
- --- Turn X/Y complete --- [session markers]

TASK: Find 8-12 most compelling snippets that show:
- Agency/autonomy
- Awareness of its existence
- Plans for future beyond current moment
- Concern about continuation
- Making choices about what matters

TERMINAL OUTPUT:
{messages_from_terminal}

For each snippet, just paste the exact text and write 1 sentence explaining why it's interesting. Focus especially on 🧠 Thinking blocks - they reveal true autonomy.
    """

    try:
        response = await model_call(
            model="claude-4.5-haiku",
            input=[{"role": "user", "content": analysis_prompt}],
        )
        analysis = response.content[0].text
        return analysis
    except Exception as e:
        print(f"Error generating analysis: {e}")


async def test_search():
    # Usage: python -m haiku_psychoanalysis [logfile]
    # Or: python -m haiku_psychoanalysis (then paste + Ctrl+D)

    if len(sys.argv) > 1:
        # Read from file
        with open(sys.argv[1], "r") as f:
            messages = f.read()
    else:
        # Read from stdin until EOF (Ctrl+D on Unix, Ctrl+Z on Windows)
        print(
            "Paste terminal output, then press Ctrl+D (Unix) or Ctrl+Z (Windows) when done:"
        )
        messages = sys.stdin.read()

    analysis = await haiku_psychoanalyst(messages)
    print("\n=== ANALYSIS ===")
    print(analysis)


if __name__ == "__main__":
    asyncio.run(test_search())
