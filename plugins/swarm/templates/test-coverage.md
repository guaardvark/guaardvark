# Swarm Plan: Test Coverage Blitz

Spawn agents to write tests for untested modules. Each agent owns
one module — no conflicts, maximum parallelism.

Edit the task list below to match the modules you want covered.

## Task: Test the indexing service
- files: backend/tests/test_indexing_service.py
- depends_on: none

Write comprehensive tests for backend/services/indexing_service.py.
Cover normal operation, edge cases, and error handling.
Mock external dependencies (LlamaIndex, Ollama) but test real logic.

## Task: Test the agent executor
- files: backend/tests/test_agent_executor.py
- depends_on: none

Write tests for backend/services/agent_executor.py.
Test tool dispatch, error recovery, and multi-step execution.

## Task: Test the batch image generator
- files: backend/tests/test_batch_image_generator.py
- depends_on: none

Write tests for backend/services/batch_image_generator.py.
Test job creation, status tracking, and failure handling.
Mock GPU operations — we're testing logic, not CUDA.

## Task: Test the chat utilities
- files: backend/tests/test_chat_utils.py
- depends_on: none

Write tests for backend/utils/chat_utils.py.
Test message formatting, token counting, context window management.
