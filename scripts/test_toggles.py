#!/usr/bin/env python3
"""
Guaardvark Toggle Test Harness
==============================
Tests whether the developer toggles actually produce measurably different results.
Uses local Ollama only — zero paid tokens.

Usage:
    python3 scripts/test_toggles.py                    # Run full test
    python3 scripts/test_toggles.py --quick             # Quick test (5 queries)
    python3 scripts/test_toggles.py --toggle advanced_rag  # Test specific toggle
    python3 scripts/test_toggles.py --report            # Show last results

Results saved to: data/toggle_test_results/
"""

import argparse
import json
import os
import sys
import time
import hashlib
import statistics
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# --- Configuration ---
BACKEND_URL = os.environ.get("GUAARDVARK_API_URL", "http://localhost:5002/api")
RESULTS_DIR = Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "toggle_test_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Color output
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
WHITE = "\033[1;37m"
DIM = "\033[2m"
NC = "\033[0m"
BOLD = "\033[1m"


# --- Test Queries ---
# Three categories: document-grounded (needs RAG), conversational (no RAG), multi-turn (needs context)

DOCUMENT_QUERIES = [
    # These should return different results with RAG on vs off
    {"message": "What files are in the GUAARDVARK folder?", "category": "rag", "expects_rag": True},
    {"message": "Explain the stop.sh script and what it does", "category": "rag", "expects_rag": True},
    {"message": "What programming languages are used in this project?", "category": "rag", "expects_rag": True},
    {"message": "How does the backend handle database migrations?", "category": "rag", "expects_rag": True},
    {"message": "What is the purpose of the celery workers?", "category": "rag", "expects_rag": True},
    {"message": "Describe the frontend architecture", "category": "rag", "expects_rag": True},
    {"message": "What models are available for text generation?", "category": "rag", "expects_rag": True},
    {"message": "How does the plugin system work?", "category": "rag", "expects_rag": True},
]

CONVERSATIONAL_QUERIES = [
    # These should work similarly with or without RAG
    {"message": "Hello, how are you today?", "category": "conversational", "expects_rag": False},
    {"message": "What is 2 + 2?", "category": "conversational", "expects_rag": False},
    {"message": "Write a haiku about coding", "category": "conversational", "expects_rag": False},
    {"message": "Explain what an API is in simple terms", "category": "conversational", "expects_rag": False},
]

CONTEXT_QUERIES = [
    # Multi-turn: second query depends on first
    {"message": "My name is TestBot and I'm testing the context window", "category": "context_setup", "expects_rag": False},
    {"message": "What is my name?", "category": "context_recall", "expects_rag": False},
    {"message": "I'm working on a project called Zephyr that uses Rust", "category": "context_setup", "expects_rag": False},
    {"message": "What language does my project use?", "category": "context_recall", "expects_rag": False},
]

ALL_QUERIES = DOCUMENT_QUERIES + CONVERSATIONAL_QUERIES + CONTEXT_QUERIES
QUICK_QUERIES = DOCUMENT_QUERIES[:3] + CONVERSATIONAL_QUERIES[:1] + CONTEXT_QUERIES[:2]


# --- Toggle Definitions ---
TOGGLES = {
    "advanced_rag": {
        "name": "Advanced RAG",
        "setting_key": "advanced_rag_enabled",
        "api_endpoint": "/settings/rag-features",
        "api_field": "advanced_rag_enabled",
        "requires_restart": True,
        "env_var": "GUAARDVARK_ADVANCED_RAG",
        "description": "Vector retrieval + intelligent code chunking vs SimpleChatEngine fallback",
    },
    "enhanced_context": {
        "name": "Enhanced Context",
        "setting_key": "enhanced_context_enabled",
        "api_endpoint": "/settings/rag-features",
        "api_field": "enhanced_context_enabled",
        "requires_restart": True,
        "env_var": "GUAARDVARK_ENHANCED_CONTEXT",
        "description": "16K token window + disk persistence vs 8K in-memory only",
    },
    "behavior_learning": {
        "name": "Behavior Learning",
        "setting_key": "behavior_learning_enabled",
        "api_endpoint": "/settings/behavior_learning",
        "api_field": "enabled",
        "requires_restart": False,
        "env_var": None,
        "description": "User behavior logging to JSONL + /saverule command",
    },
    "rag_debug": {
        "name": "RAG Debug",
        "setting_key": "rag_debug_enabled",
        "api_endpoint": "/settings/rag-features",
        "api_field": "rag_debug_enabled",
        "requires_restart": False,
        "env_var": "GUAARDVARK_RAG_DEBUG",
        "description": "RAG debug endpoints (always live regardless — dead switch)",
    },
    "verbose_logging": {
        "name": "Verbose Logging",
        "setting_key": "advanced_debug",
        "api_endpoint": "/settings/advanced_debug",
        "api_field": "enabled",
        "requires_restart": False,
        "env_var": None,
        "description": "Root logger → DEBUG, rotating debug log file",
    },
    "llm_debug": {
        "name": "LLM Debug",
        "setting_key": "llm_debug",
        "api_endpoint": "/settings/llm_debug",
        "api_field": "enabled",
        "requires_restart": False,
        "env_var": None,
        "description": "Full prompt/response/tool logging to logs/llm_debug.log",
    },
}


# --- API Helpers ---
def api_get(path: str) -> dict:
    try:
        r = requests.get(f"{BACKEND_URL}{path}", timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def api_post(path: str, data: dict = None) -> dict:
    try:
        r = requests.post(f"{BACKEND_URL}{path}", json=data or {}, timeout=120)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def check_backend():
    """Verify backend is running."""
    try:
        r = requests.get(f"{BACKEND_URL}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def get_toggle_state(toggle_key: str) -> Optional[bool]:
    """Read current state of a toggle."""
    toggle = TOGGLES[toggle_key]
    resp = api_get(toggle["api_endpoint"])
    data = resp.get("data", resp)
    if isinstance(data, dict):
        return data.get(toggle["api_field"])
    return None


def send_chat(message: str, session_id: str, use_rag: bool = True) -> dict:
    """Send a chat message and capture detailed metrics."""
    start = time.time()
    resp = api_post("/enhanced-chat", {
        "message": message,
        "session_id": session_id,
        "use_rag": use_rag,
        "voice_mode": False,
    })
    elapsed = time.time() - start

    data = resp.get("data", resp)
    if isinstance(data, str):
        data = {"response": data}

    return {
        "response": data.get("response", data.get("error", "NO RESPONSE")),
        "response_time_ms": round(elapsed * 1000, 1),
        "model_used": data.get("model_used", "unknown"),
        "rag_context": data.get("rag_context", []),
        "rag_context_count": len(data.get("rag_context", [])),
        "token_usage": data.get("token_usage", {}),
        "simple_mode_used": data.get("simple_mode_used", False),
        "web_search_used": data.get("web_search_used", False),
        "response_length": len(data.get("response", "")),
    }


# --- Scoring ---
def score_response(query: dict, result: dict) -> dict:
    """Score a response on multiple dimensions."""
    response = result["response"].lower()
    scores = {}

    # Relevance: does the response address the question?
    question_words = set(query["message"].lower().split())
    response_words = set(response.split())
    overlap = len(question_words & response_words)
    scores["word_overlap"] = overlap

    # Length: longer responses generally indicate more engagement
    scores["response_length"] = result["response_length"]

    # RAG grounding: did it use retrieved context?
    scores["rag_chunks_used"] = result["rag_context_count"]
    scores["used_rag"] = result["rag_context_count"] > 0

    # Specificity markers: does the response contain specific details vs generic fluff?
    specificity_markers = [
        "file", "function", "class", "module", "endpoint", "api",
        "database", "table", "column", "migration", "config",
        "port", "service", "docker", "redis", "celery", "flask",
        "react", "vite", "ollama", "whisper", "piper",
    ]
    specific_count = sum(1 for marker in specificity_markers if marker in response)
    scores["specificity"] = specific_count

    # Hedging: generic responses often hedge
    hedge_markers = [
        "i'm not sure", "it's unclear", "i don't have", "without more",
        "it appears", "it seems", "might be", "could be",
        "based on the code files provided", "not a widely known",
    ]
    hedge_count = sum(1 for h in hedge_markers if h in response)
    scores["hedging"] = hedge_count

    # Context recall (for multi-turn tests)
    if query["category"] == "context_recall":
        if "testbot" in response:
            scores["context_recalled"] = True
        elif "zephyr" in response or "rust" in response:
            scores["context_recalled"] = True
        else:
            scores["context_recalled"] = False

    # Confidence: inverse of hedging, weighted by specificity
    scores["confidence"] = max(0, scores["specificity"] - scores["hedging"] * 2)

    return scores


# --- Test Runner ---
def run_test_suite(queries: list, label: str, session_prefix: str) -> dict:
    """Run a full test suite and return results."""
    print(f"\n{CYAN}  Running {len(queries)} queries ({label}){NC}")
    print(f"  {'─' * 60}")

    results = []
    session_id = f"toggle_test_{session_prefix}_{int(time.time())}"

    for i, query in enumerate(queries):
        q = query["message"]
        display_q = q[:60] + "..." if len(q) > 60 else q
        sys.stdout.write(f"  {DIM}[{i+1}/{len(queries)}]{NC} {display_q}")
        sys.stdout.flush()

        result = send_chat(query["message"], session_id, use_rag=True)
        scores = score_response(query, result)

        # Color the response time
        rt = result["response_time_ms"]
        rt_color = GREEN if rt < 3000 else YELLOW if rt < 10000 else RED
        rag_indicator = f"{GREEN}RAG:{result['rag_context_count']}{NC}" if result["rag_context_count"] > 0 else f"{DIM}no-rag{NC}"

        print(f" {rt_color}{rt:.0f}ms{NC} {rag_indicator} spec:{scores['specificity']} hedge:{scores['hedging']}")

        results.append({
            "query": query,
            "result": result,
            "scores": scores,
        })

    return {
        "label": label,
        "timestamp": datetime.now().isoformat(),
        "session_id": session_id,
        "query_count": len(queries),
        "results": results,
        "summary": compute_summary(results),
    }


def compute_summary(results: list) -> dict:
    """Compute aggregate metrics from a test run."""
    times = [r["result"]["response_time_ms"] for r in results]
    specificity = [r["scores"]["specificity"] for r in results]
    hedging = [r["scores"]["hedging"] for r in results]
    confidence = [r["scores"]["confidence"] for r in results]
    rag_counts = [r["scores"]["rag_chunks_used"] for r in results]
    lengths = [r["scores"]["response_length"] for r in results]

    # Context recall for multi-turn tests
    context_results = [r for r in results if r["query"]["category"] == "context_recall"]
    context_recalled = sum(1 for r in context_results if r["scores"].get("context_recalled", False))

    return {
        "avg_response_time_ms": round(statistics.mean(times), 1) if times else 0,
        "median_response_time_ms": round(statistics.median(times), 1) if times else 0,
        "p95_response_time_ms": round(sorted(times)[int(len(times) * 0.95)] if times else 0, 1),
        "avg_specificity": round(statistics.mean(specificity), 2) if specificity else 0,
        "avg_hedging": round(statistics.mean(hedging), 2) if hedging else 0,
        "avg_confidence": round(statistics.mean(confidence), 2) if confidence else 0,
        "avg_rag_chunks": round(statistics.mean(rag_counts), 2) if rag_counts else 0,
        "avg_response_length": round(statistics.mean(lengths), 0) if lengths else 0,
        "total_rag_retrievals": sum(1 for r in rag_counts if r > 0),
        "context_recall_rate": f"{context_recalled}/{len(context_results)}" if context_results else "N/A",
    }


# --- Comparison ---
def compare_runs(run_a: dict, run_b: dict):
    """Print a side-by-side comparison of two runs."""
    a = run_a["summary"]
    b = run_b["summary"]
    label_a = run_a["label"]
    label_b = run_b["label"]

    print(f"\n{RED}{'━' * 70}{NC}")
    print(f"{WHITE}{BOLD}  COMPARISON: {label_a} vs {label_b}{NC}")
    print(f"{RED}{'━' * 70}{NC}\n")

    def delta(key, higher_is_better=True):
        va, vb = a[key], b[key]
        if isinstance(va, str) or isinstance(vb, str):
            return f"{va} → {vb}"
        diff = vb - va
        pct = (diff / va * 100) if va != 0 else 0
        arrow = "↑" if diff > 0 else "↓" if diff < 0 else "="
        color = GREEN if (diff > 0) == higher_is_better else RED if diff != 0 else DIM
        return f"{va:>8.1f}  →  {vb:<8.1f}  {color}{arrow} {abs(diff):.1f} ({abs(pct):.0f}%){NC}"

    rows = [
        ("Avg Response Time (ms)", "avg_response_time_ms", False),
        ("Median Response Time (ms)", "median_response_time_ms", False),
        ("P95 Response Time (ms)", "p95_response_time_ms", False),
        ("Avg Specificity Score", "avg_specificity", True),
        ("Avg Hedging Score", "avg_hedging", False),
        ("Avg Confidence Score", "avg_confidence", True),
        ("Avg RAG Chunks Retrieved", "avg_rag_chunks", True),
        ("Avg Response Length (chars)", "avg_response_length", True),
        ("Total RAG Retrievals", "total_rag_retrievals", True),
    ]

    print(f"  {'Metric':<30s}  {label_a:>10s}  →  {label_b:<10s}  {'Change':>20s}")
    print(f"  {'─' * 80}")

    for label, key, higher_better in rows:
        print(f"  {label:<30s}  {delta(key, higher_better)}")

    # Context recall
    print(f"  {'Context Recall':<30s}  {a['context_recall_rate']:>10s}  →  {b['context_recall_rate']:<10s}")

    # Per-query comparison for RAG queries
    print(f"\n{CYAN}  Per-Query RAG Comparison:{NC}")
    print(f"  {'Query':<45s} {'A chunks':>8s} {'B chunks':>8s} {'A spec':>6s} {'B spec':>6s}")
    print(f"  {'─' * 75}")

    for i, (ra, rb) in enumerate(zip(run_a["results"], run_b["results"])):
        if ra["query"]["category"] != "rag":
            continue
        q = ra["query"]["message"][:42]
        ac = ra["scores"]["rag_chunks_used"]
        bc = rb["scores"]["rag_chunks_used"]
        asp = ra["scores"]["specificity"]
        bsp = rb["scores"]["specificity"]
        chunk_color = GREEN if bc > ac else RED if bc < ac else DIM
        spec_color = GREEN if bsp > asp else RED if bsp < asp else DIM
        print(f"  {q:<45s} {ac:>8d} {chunk_color}{bc:>8d}{NC} {asp:>6d} {spec_color}{bsp:>6d}{NC}")

    print()


# --- Report ---
def show_report():
    """Show results from the last test run."""
    result_files = sorted(RESULTS_DIR.glob("*.json"), reverse=True)
    if not result_files:
        print(f"{RED}No test results found in {RESULTS_DIR}{NC}")
        return

    print(f"\n{CYAN}  Available test results:{NC}")
    for f in result_files[:10]:
        data = json.loads(f.read_text())
        label = data.get("label", "unknown")
        ts = data.get("timestamp", "?")[:19]
        qcount = data.get("query_count", 0)
        summary = data.get("summary", {})
        avg_time = summary.get("avg_response_time_ms", 0)
        avg_spec = summary.get("avg_specificity", 0)
        print(f"  {DIM}{ts}{NC}  {WHITE}{label:<30s}{NC}  {qcount} queries  avg:{avg_time:.0f}ms  spec:{avg_spec:.1f}")

    # Auto-compare if we have a pair
    if len(result_files) >= 2:
        latest = json.loads(result_files[0].read_text())
        previous = json.loads(result_files[1].read_text())
        compare_runs(previous, latest)


# --- Main ---
def main():
    parser = argparse.ArgumentParser(description="Guaardvark Toggle Test Harness")
    parser.add_argument("--quick", action="store_true", help="Run quick test (5 queries)")
    parser.add_argument("--toggle", type=str, help="Test a specific toggle (e.g. advanced_rag)")
    parser.add_argument("--report", action="store_true", help="Show last results")
    parser.add_argument("--compare", nargs=2, help="Compare two result files")
    parser.add_argument("--label", type=str, help="Custom label for this run")
    parser.add_argument("--no-rag", action="store_true", help="Run with use_rag=False (baseline)")
    args = parser.parse_args()

    if args.report:
        show_report()
        return

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text())
        b = json.loads(Path(args.compare[1]).read_text())
        compare_runs(a, b)
        return

    # Check backend
    if not check_backend():
        print(f"{RED}Backend not reachable at {BACKEND_URL}{NC}")
        print(f"Start it with: ./start.sh --fast")
        sys.exit(1)

    queries = QUICK_QUERIES if args.quick else ALL_QUERIES
    label = args.label or f"test_{datetime.now().strftime('%H%M%S')}"

    if args.toggle:
        if args.toggle not in TOGGLES:
            print(f"{RED}Unknown toggle: {args.toggle}{NC}")
            print(f"Available: {', '.join(TOGGLES.keys())}")
            sys.exit(1)
        toggle = TOGGLES[args.toggle]
        state = get_toggle_state(args.toggle)
        label = f"{toggle['name']}_{'ON' if state else 'OFF'}"
        print(f"\n{WHITE}{BOLD}Testing toggle: {toggle['name']}{NC}")
        print(f"  Current state: {'ON' if state else 'OFF'}")
        print(f"  Effect: {toggle['description']}")
        if toggle["requires_restart"]:
            print(f"  {YELLOW}Note: This toggle requires a backend restart to take effect{NC}")
            print(f"  {YELLOW}Run this test, restart with toggle flipped, run again, then --report{NC}")

    print(f"\n{RED}{'━' * 70}{NC}")
    print(f"{WHITE}{BOLD}  GUAARDVARK TOGGLE TEST HARNESS{NC}")
    print(f"{RED}{'━' * 70}{NC}")
    print(f"  {DIM}Backend:{NC} {BACKEND_URL}")
    print(f"  {DIM}Queries:{NC} {len(queries)}")
    print(f"  {DIM}Label:{NC} {label}")
    print(f"  {DIM}Time:{NC} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Get current system state
    health = api_get("/health")
    model_info = api_get("/meta/llm-ready")
    rag_features = api_get("/settings/rag-features")

    print(f"\n{CYAN}  System State:{NC}")
    print(f"  Model: {model_info.get('data', model_info).get('model', 'unknown')}")
    rag_data = rag_features.get("data", rag_features)
    if isinstance(rag_data, dict):
        print(f"  Enhanced Context: {'ON' if rag_data.get('enhanced_context_enabled') else 'OFF'}")
        print(f"  Advanced RAG: {'ON' if rag_data.get('advanced_rag_enabled') else 'OFF'}")
        print(f"  RAG Debug: {'ON' if rag_data.get('rag_debug_enabled') else 'OFF'}")

    # Run the test
    results = run_test_suite(queries, label, label)

    # Add system state to results
    results["system_state"] = {
        "model": model_info.get("data", model_info).get("model", "unknown"),
        "rag_features": rag_data if isinstance(rag_data, dict) else {},
        "backend_url": BACKEND_URL,
    }

    # Save results
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{label.replace(' ', '_')}.json"
    result_path = RESULTS_DIR / filename
    result_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n  {GREEN}Results saved:{NC} {result_path}")

    # Print summary
    s = results["summary"]
    print(f"\n{CYAN}  Summary:{NC}")
    print(f"  Avg response time:  {s['avg_response_time_ms']:.0f}ms")
    print(f"  Avg specificity:    {s['avg_specificity']:.1f}")
    print(f"  Avg hedging:        {s['avg_hedging']:.1f}")
    print(f"  Avg confidence:     {s['avg_confidence']:.1f}")
    print(f"  RAG retrievals:     {s['total_rag_retrievals']}/{results['query_count']}")
    print(f"  Context recall:     {s['context_recall_rate']}")
    print(f"  Avg response len:   {s['avg_response_length']:.0f} chars")

    # Check for previous results to auto-compare
    result_files = sorted(RESULTS_DIR.glob("*.json"), reverse=True)
    if len(result_files) >= 2:
        previous = json.loads(result_files[1].read_text())
        print(f"\n  {DIM}Auto-comparing with previous run: {previous['label']}{NC}")
        compare_runs(previous, results)
    else:
        print(f"\n  {DIM}Run again with different toggle settings to compare.{NC}")
        print(f"  {DIM}Usage: python3 scripts/test_toggles.py --report{NC}")


if __name__ == "__main__":
    main()
