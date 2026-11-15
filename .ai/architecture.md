# Application Architecture

## High-Level Architecture
This Django application is structured as a modular monolith. It adheres strictly to the separation of concerns, heavily utilizing a **Service Layer** architecture to keep models and views thin.

### Core Stack
- **Framework**: Django REST Framework (DRF)
- **Database**: PostgreSQL (production) / SQLite (local development proxy)
- **Caching**: Redis (intended for production caching and Celery backend)
- **Task Queue**: Celery (intended for background tasks, emails, report generation)
- **Infrastructure**: Dockerized (PostgreSQL, Redis, Web App, Nginx, Celery workers)

## Architectural Patterns
### 1. Service Layer Pattern
- **`services.py`**: All business logic, external API calls, complex state mutations, and model creations happen here. Views NEVER contain business logic.
- **`selectors.py`**: Complex database queries and data aggregation are isolated here. If a query requires multiple joins (`select_related`, `prefetch_related`) or annotations, it belongs in a selector, not directly in a view.
- **`views.py`**: Thin controllers. They handle HTTP requests, deserialize input via serializers, call appropriate services/selectors, and return serialized HTTP responses.

### 2. Apps Structure
The application is divided into independent vertical slices (`apps/`), each representing a bounded context (e.g., `users`, `jobs`, `posts`, `communities`, `business`).

### 3. Custom User Model and Authentication
- Authentication is handled via JWT (JSON Web Tokens) or standard Token Authentication (verify via `settings.py`).
- The `User` model is customized to allow authentication via email or phone rather than a standard username.

### 4. Read vs Write Separation
- **Selectors (READ-ONLY)**: Exclusively return data. Never mutate state.
- **Services (WRITE/MUTATION)**: Exclusively mutate state. May return the mutated object or ID, but should not act as complex data fetchers for views.

## Performance and Scalability Standards
- **Database Optimization**: Strict adherence to preventing N+1 queries. Heavy use of `select_related` for foreign keys and `prefetch_related` for many-to-many and reverse relations.
- **Background Processing**: Synchronous web requests must NOT block for heavy I/O. Tasks like notifications, email dispatching, and heavy data processing are offloaded to background queues.
- **Pagination**: All list endpoints must be paginated to prevent memory exhaustion on large datasets.

## Known Risks and Tech Debt
- *(To be updated by AI during active audits)*: Pending deep analysis of current Celery implementation and ORM query efficiency in high-load endpoints.

## STRICT ARCHITECTURAL CONSTRAINTS

This project strictly enforces the following architectural boundaries:

- no business logic in views
- no ORM-heavy logic in serializers
- services are the only layer for state mutation
- selectors are the only layer for complex querying
- cross-app access must go through services/selectors
- transaction bounds (`transaction.atomic`) belong strictly in services, never in views or serializers

*Violating any of these architectural boundaries is explicitly UNACCEPTABLE.*
