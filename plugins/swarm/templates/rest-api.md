# Swarm Plan: REST API Scaffold

Build a complete CRUD REST API with models, routes, services, and tests.
Customize the entity names and fields below before launching.

## Task: Create database models
- files: backend/models.py
- depends_on: none

Add SQLAlchemy models for the new entities. Include proper relationships,
indexes, and sensible defaults. Follow existing model patterns in the file.

## Task: Build API routes
- files: backend/api/new_feature_api.py
- depends_on: create-database-models

Create a Flask Blueprint with CRUD endpoints:
- GET /api/{entity} — list with pagination
- GET /api/{entity}/:id — get by ID
- POST /api/{entity} — create
- PUT /api/{entity}/:id — update
- DELETE /api/{entity}/:id — delete

Follow the existing API patterns (success_response/error_response utils).

## Task: Build service layer
- files: backend/services/new_feature_service.py
- depends_on: create-database-models

Business logic layer between the API and database. Handle validation,
authorization checks, and any complex operations. Keep the API routes thin.

## Task: Write tests
- files: backend/tests/test_new_feature.py
- depends_on: create-database-models

Write pytest tests covering:
- Happy path for all CRUD operations
- Validation errors (missing fields, bad types)
- Not found cases
- Edge cases specific to the entity
