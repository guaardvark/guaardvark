# Swarm Plan: Flight Mode Demo

A simple 3-task plan that works fully offline. Perfect for testing
the swarm system with local Ollama models before going big.

Launch with: python swarm_cli.py launch templates/flight-mode-demo.md --flight-mode

## Task: Create a hello world module

Create a Python module at `demo/hello.py` with:
- A `greet(name: str) -> str` function that returns a greeting
- A `farewell(name: str) -> str` function that returns a goodbye
- A simple CLI entry point that greets the user

Keep it simple — this is just to prove the swarm works.

## Task: Write tests for hello module
- depends_on: create-a-hello-world-module

Create `demo/test_hello.py` with pytest tests:
- Test greet() returns expected strings
- Test farewell() returns expected strings
- Test edge cases (empty string, None)

## Task: Create a README

Create `demo/README.md` documenting:
- What this demo does
- How to run it
- How to run the tests
- That it was built by a swarm of AI agents (because that's cool)
