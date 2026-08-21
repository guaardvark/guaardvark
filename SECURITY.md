# Security Policy

Guaardvark runs autonomous agents, executes generated code behind review
gates, and manages GPU services on the machine it is installed on. We take
reports about anything that could break those guarantees seriously.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** (the "Report a
vulnerability" button under the repository's Security tab). That opens a
private thread with the maintainers — do **not** open a public issue for
anything exploitable.

You can expect an acknowledgement within a few days. Please include steps to
reproduce and, when relevant, which surface is involved (backend API, agent
loop, MCP server, CLI, plugin, installer).

## Scope notes

- Everything runs locally with the installing user's privileges by design;
  reports about local misuse by that same user are usually not
  vulnerabilities. Reports about the system exceeding its documented gates
  (codebase lock, pending-fixes queue, consent gates, MCP default-deny,
  outreach approval flow, kill switches) absolutely are.
- The Interconnector is opt-in and API-key authenticated; anything that lets
  one node act on another without that key is in scope.
- Third-party model weights and upstream tools (Ollama, ComfyUI, etc.) should
  be reported upstream, but we want to know if Guaardvark's integration
  weakens their defaults.

## Supported versions

Security fixes land on `main` and ship in the next release; the latest
release line is what we support.
