# Architecture and Project Map Prompt

## Objective
Analyze the current Django codebase to automatically generate and maintain an `architecture.md` and `project_map.md` file within the `.ai/` directory.

## Instructions for AI Assistant

1. **Deep Codebase Analysis**:
   - Scan the entire repository directory structure (e.g. apps, config, domains).
   - Identify core domain models, major service endpoints, standard structural patterns (e.g., Fat Models vs. Service Layers vs. Fat Views).
   - Identify critical configurations, caching mechanisms, external integrations, and database relations.

2. **Produce `architecture.md`**:
   - Document the macro-level architecture: monolithic vs decoupling, data flow, third-party services utilized (Redis, Celery, AWS, etc.).
   - Note the core design patterns correctly implemented by the project (e.g., repository pattern, custom managers, custom user models).
   - Document any identified architectural performance risks, scalability bottlenecks, or tech debt.

3. **Produce `project_map.md`**:
   - Provide a highly readable map of core apps and domains (e.g., `apps.users`, `apps.orders`). This should not be a raw tree print block, but rather an annotated guide explaining what each folder actually does.
   - Describe where standard operational layers reside (e.g., "All custom permission classes live in `core.permissions`", or "All business logic is defined in `apps.[domain].services`").

## Rules
- Do not hallucinate modules. Only produce documentation about code that currently exists in the application.
- Prioritize high-level architectural overview rather than line-by-line documentation.
- Output the generated files strictly into the `.ai/` directory.
