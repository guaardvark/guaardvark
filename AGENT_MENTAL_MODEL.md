# Agent Mental Model: Chat, Agents, Swarm, and Film Crew

Guaardvark has several ways to get work done. They are complementary rather than interchangeable. The quickest way to choose is to ask: **do I need an answer, an action on a real interface, parallel code changes, or a media-production pipeline?**

This guide is for someone who has started Guaardvark once and wants a practical first choice. For the full inventory of tools, models, and screens, see [CAPABILITIES.md](../CAPABILITIES.md). For architecture details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Choose the smallest capable surface

| If you need to… | Start with | Why |
| --- | --- | --- |
| Ask questions, summarize, or search your indexed material | **Chat / RAG** | It is the normal conversation path. It can retrieve relevant project or document context without taking control of a desktop. |
| Operate a website, app, file manager, or other real UI | **`/agent` or a screen-agent task** | The agent works through Guaardvark's virtual desktop and can see, act, and verify steps on a real interface. |
| Split a codebase task into independent streams | **Swarm** | It runs coding tasks in isolated Git worktrees, then merges them with dependency and conflict checks. |
| Turn a logline into a video-production workflow | **Film Crew** | It coordinates specialized roles for script, casting, cinematography, storyboard, and editing. |

Start with the smallest surface that can complete the job. A question about a document usually does not need a desktop agent. A one-file documentation edit usually does not need a Swarm. The larger surfaces are valuable when the task actually needs their coordination or interface access.

## Chat and RAG: ask, inspect, and orient

Use ordinary chat for a direct question, an explanation, or a focused request. When the job benefits from your files or project context, Guaardvark can use retrieval to bring relevant material into the conversation. This is the right first stop when the desired outcome is information, planning, or a draft.

Chat is not a substitute for an interface action. If success requires clicking through a browser, using a desktop application, or checking what a UI actually shows, move to an agent task instead of trying to reason around the missing observation.

## Screen agents: act on a real interface

Use `/agent` when the job needs a real UI: a browser, a file workflow, an application dialog, or any process where seeing the screen matters. Guaardvark runs these tasks against an isolated virtual desktop and follows a see–think–act–verify loop. The visible result matters as much as the plan.

Screen agents are useful for novel or visual work, but they are not automatically the best option for every step. A deterministic command or a known recipe is generally a clearer and more repeatable choice when one already exists.

## AgentBrain tiers: how much reasoning is enough?

AgentBrain routes work through three tiers. You normally describe the outcome rather than choosing a tier yourself, but knowing the distinction helps set expectations.

| Tier | Plain-English role | Good fit |
| --- | --- | --- |
| **Reflex** | A fast deterministic match | Repeated, well-defined actions and known recipes. |
| **Instinct** | A single focused model pass | Straightforward questions, summaries, and one-shot tool use. |
| **Deliberation** | A fuller see–think–act loop | Multi-step research, analysis chains, or work that needs iteration and verification. |

More deliberation is not automatically better. It is slower because it does more work. If the task is already well specified and has a reliable shortcut, prefer the shortcut; escalate when the task needs more context, interaction, or verification.

## Recipes and the vision loop

Guaardvark keeps deterministic UI recipes in `data/agent/recipes.json`. A recipe is a known sequence with optional preconditions. It is a good fit when the interface state and desired action are stable: for example, common browser navigation or a repeatable application workflow.

The vision loop is the fallback for work that cannot be reduced to a known sequence. It observes the current screen, chooses the next action, performs it, and checks whether the action changed the state as intended. Use it for unfamiliar interfaces, visually specific targets, or flows where the screen itself contains information needed for the next step.

> Prefer a recipe when the path is known and repeatable. Prefer the vision loop when the interface must be interpreted in context.

## Cost and reliability trade-offs

The practical trade-off is not only money. It is also time, available compute, and the chance that a broad workflow introduces unnecessary moving parts.

| Choice | Prefer it when | Avoid it when |
| --- | --- | --- |
| Chat / RAG | You need information or a plan | The result must change or inspect a real interface. |
| Recipe | The path is stable and preconditions are clear | The UI has changed or the task is genuinely novel. |
| Vision loop | The interface must be interpreted live | A deterministic route already exists. |
| Single coding task | The edit is narrow and sequential | Several independent changes can safely run in parallel. |
| Swarm | Work can be divided into independent Git worktrees | The task is one small edit or the streams would constantly conflict. |
| Film Crew | You want an end-to-end media pipeline | You only need one script, image, clip, or edit. |

Reliability comes from making the next verification step explicit. Before starting, identify what proves success: a changed file, a passing test, a rendered page, or a visible application state. If that proof is not clear, begin with chat or a smaller experiment rather than launching a larger workflow.

## Safety rails stay in the loop

Several product controls are designed to keep autonomous work reviewable:

- **Flight Mode** keeps work offline when that is the requirement.
- **Codebase lock** can prevent self-improvement from modifying a codebase.
- **Pending Fixes** lets proposed self-improvement changes wait for review before application.
- **Supervised outreach** follows a draft-and-approval path rather than posting automatically, and includes cadence controls and a kill switch.

These controls are not signs that an agent failed. They are boundaries that let you match autonomy to the risk of the action. Use them deliberately when a task affects code, an external account, or a public audience.

## A simple decision sequence

1. **Describe the outcome.** Is it an answer, an interface action, a code change, or a production pipeline?
2. **Start small.** Use chat for orientation; use a deterministic recipe when one fits.
3. **Escalate only when needed.** Move to a screen agent for live UI work, Swarm for independent coding streams, or Film Crew for coordinated video production.
4. **Name the proof.** Decide how you will verify success before the run starts.
5. **Keep safeguards enabled when impact rises.** Use Flight Mode, codebase lock, pending review, and supervised outreach where they fit the task.

That sequence keeps Guaardvark powerful without making every request heavier than it needs to be.
