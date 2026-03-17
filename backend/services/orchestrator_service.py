#!/usr/bin/env python3
"""
Orchestrator Service
Manages high-level planning and delegation of tasks to specialized agents.

Execution model: dependency-aware DAG execution.
  - Tasks are grouped into "waves" via topological sort (Kahn's algorithm).
  - Tasks in the same wave share no mutual dependencies and execute sequentially
    within the wave (parallel execution is a future upgrade).
  - Results from a completed task are forwarded as context to every task that
    lists it as a dependency.
  - Circular dependencies are detected before execution starts and reported.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
import json

from backend.services.agent_config import get_agent_config_manager
from backend.services.agent_executor import AgentExecutor
from backend.utils.llm_service import (
    get_default_llm,
    ChatMessage,
    MessageRole,
    _safe_content,
)

logger = logging.getLogger(__name__)


@dataclass
class SubTask:
    """A sub-task to be executed by a specific agent"""

    id: int
    description: str
    assigned_agent: str
    dependencies: List[int] = field(default_factory=list)
    status: str = "pending"  # pending | running | completed | failed
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class OrchestrationPlan:
    """A plan consisting of multiple sub-tasks"""

    original_request: str
    subtasks: List[SubTask] = field(default_factory=list)
    current_step_index: int = 0
    status: str = "planning"  # planning | executing | completed | failed


class OrchestratorService:
    """
    Orchestrates complex tasks by breaking them down and delegating to
    specialized agents, respecting inter-task dependencies.
    """

    def __init__(self):
        self.agent_config_manager = get_agent_config_manager()
        self.llm = get_default_llm()
        self._active_plans: Dict[str, OrchestrationPlan] = {}
        self._all_tools = None  # Lazily initialised once, then reused

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_request(
        self, user_request: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main entry point: analyse the request, create a dependency-aware plan,
        and execute it respecting the declared dependency order.
        """
        logger.info(f"Orchestrator processing request: {user_request[:100]}...")

        plan = self._create_plan(user_request)

        if not plan.subtasks:
            return {"success": False, "error": "Failed to generate a valid plan"}

        return self._execute_plan(plan, context)

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def _create_plan(self, request: str) -> OrchestrationPlan:
        """Use the LLM to break the request into an ordered set of sub-tasks."""
        agents = self.agent_config_manager.get_enabled_agents()
        agents_desc = "\n".join([f"- {a.id}: {a.description}" for a in agents])

        system_prompt = f"""You are an Expert Orchestrator. Your goal is to break down a complex user request into a sequence of sub-tasks.

AVAILABLE AGENTS:
{agents_desc}

RULES:
1. Analyse the user request.
2. Break it down into logical steps.
3. Assign each step to the MOST SUITABLE agent from the list.
4. Define dependencies: list IDs of steps that MUST complete before this step starts.
   - Independent steps should have an empty dependencies list.
   - Never create circular dependencies.
5. Output JSON STRICTLY in this format (no markdown, no extra text):
{{
  "steps": [
    {{
      "id": 1,
      "description": "Research the best Python library for...",
      "assigned_agent": "research_agent",
      "dependencies": []
    }},
    {{
      "id": 2,
      "description": "Write the code using the library found in step 1...",
      "assigned_agent": "code_assistant",
      "dependencies": [1]
    }}
  ]
}}
"""

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
            ChatMessage(role=MessageRole.USER, content=request),
        ]

        try:
            response = self.llm.chat(messages)
            content = _safe_content(response.message)

            # Strip optional markdown code fence
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            plan_data = json.loads(content.strip())

            subtasks = [
                SubTask(
                    id=step["id"],
                    description=step["description"],
                    assigned_agent=step["assigned_agent"],
                    dependencies=step.get("dependencies", []),
                )
                for step in plan_data.get("steps", [])
            ]

            return OrchestrationPlan(original_request=request, subtasks=subtasks)

        except Exception as e:
            logger.error(f"Failed to create plan: {e}", exc_info=True)
            return OrchestrationPlan(original_request=request)

    # ------------------------------------------------------------------
    # Dependency-aware execution
    # ------------------------------------------------------------------

    def _topological_sort(self, subtasks: List[SubTask]) -> List[List[SubTask]]:
        """
        Group subtasks into sequential execution waves using Kahn's algorithm.

        Tasks in the same wave have no mutual dependencies and can logically
        run in parallel (currently executed sequentially; parallelism is a
        future upgrade).

        Returns:
            A list of waves, where each wave is a list of SubTask objects
            that are ready to run once the previous wave completes.

        Raises:
            ValueError: if unknown dependency IDs are referenced, or if a
                        circular dependency is detected.
        """
        task_map: Dict[int, SubTask] = {t.id: t for t in subtasks}

        # Validate all referenced dependency IDs exist
        for task in subtasks:
            for dep_id in task.dependencies:
                if dep_id not in task_map:
                    raise ValueError(
                        f"Task {task.id} references unknown dependency {dep_id}"
                    )

        # in_degree[task_id] = number of unmet dependencies
        in_degree: Dict[int, int] = {t.id: len(t.dependencies) for t in subtasks}

        # dependents[task_id] = list of task_ids that depend on this task
        dependents: Dict[int, List[int]] = {t.id: [] for t in subtasks}
        for task in subtasks:
            for dep_id in task.dependencies:
                dependents[dep_id].append(task.id)

        # BFS: collect waves of zero-in-degree tasks
        waves: List[List[SubTask]] = []
        ready: List[SubTask] = [t for t in subtasks if in_degree[t.id] == 0]

        while ready:
            waves.append(list(ready))
            next_ready: List[SubTask] = []
            for task in ready:
                for dependent_id in dependents[task.id]:
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        next_ready.append(task_map[dependent_id])
            ready = next_ready

        # If not all tasks were scheduled, a cycle exists
        scheduled = sum(len(w) for w in waves)
        if scheduled != len(subtasks):
            cycle_tasks = [t.id for t in subtasks if in_degree[t.id] > 0]
            raise ValueError(
                f"Circular dependency detected among task IDs: {cycle_tasks}"
            )

        return waves

    def _execute_plan(
        self, plan: OrchestrationPlan, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute the plan respecting the dependency graph.

        Each wave's tasks are ready to run (all their dependencies have
        already completed).  Results from completed tasks are forwarded as
        context to every task that listed them as a dependency.
        """
        plan.status = "executing"
        context = context or {}

        # Map from task_id -> result string for dependency forwarding
        completed_results: Dict[int, str] = {}

        # Sort into dependency waves; catch planning errors before running anything
        try:
            waves = self._topological_sort(plan.subtasks)
        except ValueError as e:
            plan.status = "failed"
            logger.error(f"Plan dependency error: {e}")
            return {
                "success": False,
                "error": str(e),
                "plan": self._serialize_plan(plan),
            }

        logger.info(
            f"Plan has {len(plan.subtasks)} tasks across {len(waves)} execution wave(s)"
        )

        for wave_index, wave in enumerate(waves):
            logger.info(
                f"Wave {wave_index + 1}/{len(waves)}: "
                f"executing {len(wave)} task(s): {[t.id for t in wave]}"
            )

            for subtask in wave:
                plan.current_step_index = subtask.id
                subtask.status = "running"

                # Build dependency context from already-completed prerequisite tasks
                dependency_context = self._build_dependency_context(
                    subtask, completed_results
                )

                logger.info(
                    f"  → Task {subtask.id} [{subtask.assigned_agent}]: "
                    f"{subtask.description[:80]}"
                )

                result = self._delegate_to_agent(
                    agent_id=subtask.assigned_agent,
                    prompt=subtask.description,
                    context=context,
                    dependency_context=dependency_context,
                )

                if result["success"]:
                    subtask.status = "completed"
                    subtask.result = result["final_answer"]
                    completed_results[subtask.id] = subtask.result
                    logger.info(f"  ✓ Task {subtask.id} completed")
                else:
                    subtask.status = "failed"
                    subtask.error = result.get("error", "Unknown error")
                    plan.status = "failed"
                    logger.error(f"  ✗ Task {subtask.id} failed: {subtask.error}")
                    return {
                        "success": False,
                        "error": (
                            f"Step {subtask.id} "
                            f"({subtask.description[:60]}) failed: {subtask.error}"
                        ),
                        "plan": self._serialize_plan(plan),
                    }

        plan.status = "completed"

        # Synthesise a unified final answer from all step results in ID order
        results_history = [
            f"Step {t.id} — {t.assigned_agent}:\n{t.result}"
            for t in sorted(plan.subtasks, key=lambda t: t.id)
            if t.status == "completed"
        ]
        final_answer = self._synthesize_final_result(
            plan.original_request, results_history
        )

        return {
            "success": True,
            "final_answer": final_answer,
            "plan": self._serialize_plan(plan),
        }

    def _build_dependency_context(
        self, subtask: SubTask, completed_results: Dict[int, str]
    ) -> str:
        """
        Construct a human-readable context block from the results of all
        tasks that this subtask depends on.
        """
        if not subtask.dependencies:
            return ""

        parts: List[str] = []
        for dep_id in subtask.dependencies:
            result_text = completed_results.get(dep_id)
            if result_text:
                parts.append(f"[Result from Step {dep_id}]:\n{result_text}")
            else:
                # Dependency completed but produced no text (shouldn't normally happen)
                parts.append(f"[Step {dep_id} produced no output]")

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Agent delegation
    # ------------------------------------------------------------------

    def _get_all_tools(self):
        """Lazily initialise the global tool registry once and reuse it."""
        if self._all_tools is None:
            from backend.tools.tool_registry_init import initialize_all_tools

            self._all_tools = initialize_all_tools()
        return self._all_tools

    def _delegate_to_agent(
        self,
        agent_id: str,
        prompt: str,
        context: Dict[str, Any],
        dependency_context: str = "",
    ) -> Dict[str, Any]:
        """
        Delegate a single task to a specific agent.

        The dependency_context (results from prerequisite steps) is injected
        into the agent's session context so the LLM can use prior findings.
        """
        try:
            from backend.services.agent_tools import ToolRegistry

            manager = self.agent_config_manager
            agent = manager.get_agent(agent_id)
            if not agent:
                return {"success": False, "error": f"Agent '{agent_id}' not found"}

            # Build a per-agent registry containing only this agent's tools,
            # drawn from the lazily-cached global registry.
            all_tools = self._get_all_tools()
            agent_tools = ToolRegistry()
            missing = []
            for tool_name in agent.tools:
                tool = all_tools.get_tool(tool_name)
                if tool:
                    agent_tools.register(tool)
                else:
                    missing.append(tool_name)

            if missing:
                logger.warning(
                    f"Agent '{agent_id}' requested tools not in registry: {missing}"
                )

            # Compose session context: goal + dependency results + caller context
            session_parts = [
                f"[DELEGATED TASK FROM ORCHESTRATOR]",
                f"Agent: {agent.name}",
                f"Goal: {prompt}",
            ]
            if dependency_context:
                session_parts.append(
                    f"\nContext from prerequisite steps:\n{dependency_context}"
                )
            if context:
                session_parts.append(f"\nAdditional context: {context}")

            session_context = "\n".join(session_parts)

            executor = AgentExecutor(
                agent_tools, self.llm, max_iterations=agent.max_iterations
            )
            result = executor.execute(prompt, session_context=session_context)

            return {
                "success": result.success,
                "final_answer": result.final_answer,
                "error": result.error,
            }

        except Exception as e:
            logger.error(f"Delegation to '{agent_id}' failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Synthesis & serialisation
    # ------------------------------------------------------------------

    def _synthesize_final_result(self, request: str, results: List[str]) -> str:
        """Synthesise all step results into a coherent final answer."""
        prompt = (
            f"Use the results from the executed sub-tasks to answer the original "
            f"user request.\n\n"
            f"ORIGINAL REQUEST:\n{request}\n\n"
            f"SUB-TASK RESULTS:\n"
            + "\n\n".join(results)
            + "\n\nConstruct a comprehensive, well-organised final answer."
        )

        messages = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are a helpful assistant synthesising a final report.",
            ),
            ChatMessage(role=MessageRole.USER, content=prompt),
        ]

        resp = self.llm.chat(messages)
        return _safe_content(resp.message)

    def _serialize_plan(self, plan: OrchestrationPlan) -> Dict[str, Any]:
        return {
            "status": plan.status,
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "assigned_agent": s.assigned_agent,
                    "dependencies": s.dependencies,
                    "status": s.status,
                    "result": s.result,
                    "error": s.error,
                }
                for s in plan.subtasks
            ],
        }


# Global singleton
_orchestrator: Optional[OrchestratorService] = None


def get_orchestrator() -> OrchestratorService:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = OrchestratorService()
    return _orchestrator
