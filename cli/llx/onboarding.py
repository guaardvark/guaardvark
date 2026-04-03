"""First-launch onboarding wizard for guaardvark launch."""
import httpx
from rich.panel import Panel
from rich import box

from llx.launch_config import (
    load_launch_config,
    resolve_ollama_url,
    save_launch_config,
)
from llx.theme import make_console

_EMBEDDING_PATTERNS = (
    "embed", "nomic", "minilm", "bge-", "e5-", "gte-",
    "retrieval", "rerank",
)

SECURITY_NOTICE = """\
Guaardvark is a local AI platform that will:

  - Access local Ollama models for chat, RAG, and generation
  - Read and write files on your file system
  - Use GPU resources for model inference
  - In full mode: provision PostgreSQL and Redis databases

All processing happens locally. No data leaves your machine.\
"""


def format_model_size(size_bytes: int) -> str:
    """Format byte count as human-readable size."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_073_741_824:.1f} GB"
    return f"{size_bytes / 1_048_576:.1f} MB"


def fetch_ollama_models(ollama_url: str) -> list[dict]:
    """Fetch available models from Ollama, filtering out embedding models."""
    try:
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=10)
        resp.raise_for_status()
        models = resp.json().get("models", [])
    except (httpx.HTTPError, Exception):
        return []

    filtered = []
    for m in models:
        name_lower = m.get("name", "").lower()
        if any(p in name_lower for p in _EMBEDDING_PATTERNS):
            continue
        filtered.append(m)

    return filtered


def _confirm_security() -> bool:
    """Display security notice and ask for confirmation."""
    console = make_console()
    console.print()
    console.print(Panel(
        SECURITY_NOTICE,
        title="[llx.warning]Security Notice[/llx.warning]",
        border_style="llx.warning",
        box=box.ROUNDED,
        padding=(1, 2),
    ))
    console.print()

    from prompt_toolkit import prompt as pt_prompt
    answer = pt_prompt("Proceed? (Y/n) ").strip().lower()
    return answer in ("", "y", "yes")


def _pick_model(ollama_url: str) -> str | None:
    """Interactive model picker using prompt_toolkit."""
    console = make_console()
    models = fetch_ollama_models(ollama_url)

    if not models:
        console.print("[llx.warning]No models found. Is Ollama running?[/llx.warning]")
        console.print(f"[llx.dim]Checked: {ollama_url}[/llx.dim]")
        return None

    console.print("\n[llx.brand_bright]Available models:[/llx.brand_bright]")
    for i, m in enumerate(models):
        name = m.get("name", "unknown")
        size = format_model_size(m.get("size", 0))
        console.print(f"  [llx.accent]{i:>2}[/llx.accent]  {name:<30} [llx.dim]{size}[/llx.dim]")

    console.print()
    from prompt_toolkit import prompt as pt_prompt
    while True:
        answer = pt_prompt("Select model (number or name): ").strip()
        try:
            idx = int(answer)
            if 0 <= idx < len(models):
                return models[idx]["name"]
        except ValueError:
            pass
        for m in models:
            if answer in m.get("name", ""):
                return m["name"]
        console.print("[llx.error]Invalid selection. Try again.[/llx.error]")


def _pick_mode() -> str:
    """Ask user for lite vs full mode."""
    console = make_console()
    console.print("\n[llx.brand_bright]Launch mode:[/llx.brand_bright]")
    console.print("  [llx.accent] 0[/llx.accent]  Lite   [llx.dim]Instant start, SQLite, TUI only[/llx.dim]")
    console.print("  [llx.accent] 1[/llx.accent]  Full   [llx.dim]PostgreSQL, Redis, web UI at localhost:5173[/llx.dim]")
    console.print()

    from prompt_toolkit import prompt as pt_prompt
    answer = pt_prompt("Select mode (0=lite, 1=full) [0]: ").strip()
    return "full" if answer == "1" else "lite"


def run_onboarding(
    auto_yes: bool = False,
    model: str | None = None,
) -> dict:
    """Run the first-launch onboarding wizard. Returns the saved config dict."""
    console = make_console()
    ollama_url = resolve_ollama_url()

    # 1. Security notice
    if not auto_yes:
        if not _confirm_security():
            console.print("[llx.dim]Onboarding cancelled.[/llx.dim]")
            raise SystemExit(0)

    # 2. Model selection
    if model is None:
        if auto_yes:
            models = fetch_ollama_models(ollama_url)
            model = models[0]["name"] if models else None
        else:
            model = _pick_model(ollama_url)

    # 3. Mode selection
    if auto_yes:
        mode = "lite"
    else:
        mode = _pick_mode()

    # 4. Save config
    cfg = load_launch_config()
    cfg["onboarded"] = True
    cfg["model"] = model
    cfg["mode"] = mode
    cfg["ollama_base_url"] = ollama_url
    save_launch_config(cfg)

    console.print(f"\n[llx.success]Onboarding complete.[/llx.success]")
    if model:
        console.print(f"[llx.dim]Model: {model}[/llx.dim]")
    console.print(f"[llx.dim]Mode: {mode}[/llx.dim]")

    return cfg
