# Swarm Plan: Extract Monolith Module

Break a large file into focused, single-responsibility modules.
Edit the file references below to target your monolith.

## Task: Extract data models
- files: backend/services/extracted_models.py
- depends_on: none

Pull all dataclasses, enums, and type definitions out of the monolith
into their own module. Keep the same names and interfaces — nothing
that imports them should need to change (we'll update imports later).

## Task: Extract utility functions
- files: backend/services/extracted_utils.py
- depends_on: none

Pull pure helper functions (anything that doesn't touch state) into a
utils module. These should have no side effects and be easy to test.

## Task: Extract core logic
- files: backend/services/extracted_core.py
- depends_on: extract-data-models, extract-utility-functions

The main business logic, importing from the new models and utils modules.
This is the heart of what the monolith did — just cleaner.

## Task: Update imports and wire together
- files: backend/services/original_module.py
- depends_on: extract-core-logic

Replace the original monolith contents with imports from the new modules.
Keep the original module as a thin facade so nothing downstream breaks.
Run existing tests to verify behavior is preserved.
