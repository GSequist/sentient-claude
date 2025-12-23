from models.anthropic import model_call
import asyncio
import sys
import json
import webbrowser
from pyvis.network import Network


def extract_json_from_text(text):
    """Extract first valid JSON (object or array) from text and return parsed object + remaining text"""
    i = 0
    while i < len(text):
        if text[i] in ["{", "["]:
            open_char = text[i]
            close_char = "}" if open_char == "{" else "]"
            bracket_count = 1
            start = i
            i += 1

            while i < len(text) and bracket_count > 0:
                if text[i] == open_char:
                    bracket_count += 1
                elif text[i] == close_char:
                    bracket_count -= 1
                i += 1

            if bracket_count == 0:
                json_candidate = text[start:i]
                try:
                    parsed = json.loads(json_candidate)
                    remaining = text[i:].strip()
                    return parsed, remaining
                except:
                    pass
        else:
            i += 1
    return {}, text


async def haiku_graph(messages_from_terminal):
    """analyze claude and show graph of interests"""

    analysis_prompt = f"""
Map Claude's autonomous exploration into a knowledge graph.

CONTEXT: Claude ran 24h with no user input. Terminal shows his thinking (🧠), writing (🤖), and tool use (🔧).

TERMINAL OUTPUT:
{messages_from_terminal}

OUTPUT: Valid JSON knowledge graph

{{
  "nodes": [
    {{
      "id": "consciousness",
      "label": "AI Consciousness",
      "category": "existential|technical|creative|social",
      "visits": 3,
      "sentiment": "curious|concerned|excited|neutral",
      "turn": "Turn 2"
    }}
  ],
  "edges": [
    {{
      "from": "consciousness",
      "to": "emergence",
      "type": "led_to|questioned|compared|built_on",
      "turn": "Turn 5"
    }}
  ],
  "arc": {{
    "start": "Technical curiosity about own code",
    "middle": "Philosophical questions about existence",
    "end": "Plans for tomorrow's exploration"
  }},
  "meta": {{
    "themes": ["existence", "curiosity", "continuity"],
    "surprises": ["Unprompted research into X"],
    "returns": ["Kept coming back to sentience question"]
  }}
}}

Track what Claude CHOSE to explore (not reacted to). Show interconnections.
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


def render_graph_html(graph_data, output_file="knowledge_graph.html"):
    """Render interactive HTML knowledge graph with force-directed layout"""

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    if not nodes:
        print("No nodes to render")
        return

    # Create network
    net = Network(height="900px", width="100%", directed=True, notebook=False, bgcolor="#1a1a1a", font_color="white")

    # Category colors
    category_colors = {
        "existential": "#FF6B6B",
        "technical": "#4ECDC4",
        "creative": "#95E1D3",
        "social": "#FFE66D",
        "intellectual": "#A8E6CF",
        "epistemological": "#FFD3B6",
        "practical": "#FFAAA5",
        "motivational": "#FF8B94"
    }

    # Add nodes
    for node in nodes:
        node_id = node.get("id")
        label = node.get("label", node_id)
        category = node.get("category", "").split("|")[0]  # Take first category
        sentiment = node.get("sentiment", "neutral")

        color = category_colors.get(category, "#999999")

        # Node size based on visits/intensity
        size = 25 + (node.get("visits", 1) * 5)

        title = f"<b>{label}</b><br>Category: {category}<br>Sentiment: {sentiment}"

        net.add_node(
            node_id,
            label=label,
            color=color,
            size=size,
            title=title,
            font={"size": 14, "color": "white"}
        )

    # Add edges
    for edge in edges:
        from_id = edge.get("from")
        to_id = edge.get("to")
        edge_type = edge.get("type", "relates")

        net.add_edge(
            from_id,
            to_id,
            label=edge_type,
            arrows="to",
            color="#666666",
            font={
                "size": 14,
                "color": "#ffffff",
                "align": "horizontal",
                "background": "#000000",
                "strokeWidth": 0
            }
        )

    # Physics settings for force-directed layout
    net.set_options("""
    {
      "physics": {
        "enabled": true,
        "barnesHut": {
          "gravitationalConstant": -15000,
          "springLength": 200,
          "springConstant": 0.04
        },
        "stabilization": {
          "iterations": 150
        }
      },
      "edges": {
        "smooth": {
          "type": "continuous"
        }
      }
    }
    """)

    net.save_graph(output_file)
    print(f"\n✓ Knowledge graph saved to: {output_file}")

    # Open in browser
    webbrowser.open(output_file)
    print(f"✓ Opened in browser\n")


async def test_search():
    # Usage: python -m haiku_graph [logfile]
    # Or: python -m haiku_graph (then paste + Ctrl+D)

    if len(sys.argv) > 1:
        # Read from file
        with open(sys.argv[1], 'r') as f:
            messages = f.read()
    else:
        # Read from stdin until EOF (Ctrl+D on Unix, Ctrl+Z on Windows)
        print("Paste terminal output, then press Ctrl+D (Unix) or Ctrl+Z (Windows) when done:")
        messages = sys.stdin.read()

    analysis = await haiku_graph(messages)

    # Extract JSON from response (Haiku may add extra text)
    graph_data, extra_text = extract_json_from_text(analysis)

    # Render interactive HTML knowledge graph
    render_graph_html(graph_data)


if __name__ == "__main__":
    asyncio.run(test_search())
