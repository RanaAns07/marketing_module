# Project Map

This document serves as a functional directory map for the Django repository. It annotates the architecture mapped out in `architecture.md` against the physical file tree.

## Root Level
- `manage.py`: Django's command-line utility for administrative tasks.
- `requirements/`: Contains split dependency files (`base.txt`, `dev.txt`, `prod.txt`).
- `docker-compose.yml` / `docker-compose.prod.yml`: Orchestration for local development and production deployment.
- `Dockerfile` / `docker-entrypoint.sh`: Container build and initialization instructions.
- `nginx/`: Reverse proxy configuration for production.
- `.ai/`: AI assistant context directory (Coding Rules, Architecture, Prompts).

## `/config` - Global Configuration
This directory contains the root application settings.
- `settings/`: Split settings module.
  - `base.py`: Shared settings across all environments.
  - `dev.py`: Local development overrides (e.g., SQLite, debug toolbar).
  - `prod.py`: Production security overrides (e.g., PostgreSQL, secure cookies, static file hosting).
- `urls.py`: The master URL router mapping incoming requests to specific apps.
- `wsgi.py` / `asgi.py`: Entry points for WSGI and ASGI servers respectively.

## `/apps` - Bounded Contexts (Domains)
Each folder inside `/apps` is an isolated Django application representing a specific business domain.

### Standard App Directory Structure
Almost all applications follow this strict generic structure:
- `models.py`: Database schema definitions.
- `serializers.py`: Data validation and serialization (DRF).
- `views.py`: API endpoint definitions (thin layer).
- `urls.py`: URL routing specific to the app.
- `services.py`: **Core Business Logic.** Where records are created, modified, external APIs are called, and validation involving multiple models occurs.
- `selectors.py`: **Core Query Logic.** Contains complex read operations and optimized ORM chains (`select_related`, etc.).
- `managers.py`: Custom model managers enforcing base query scopes.
- `tests/`: Unit and integration testing directory.

### Specific App Domains
- **`users`**: User account management, authentication, and core profile configurations. 
- **`business`**: Enterprise or organizational level features and accounts.
- **`communities`**: Sub-groups or social clusters within the platform.
- **`events`**: Calendar scheduling, event management, and ticketing.
- **`institutes`**: Educational or organizational partner integrations.
- **`jobs`**: Job postings, applications, and professional networking data.
- **`messaging`**: Direct peer-to-peer or group chat functionality.
- **`network`**: Connections, friend requests, or professional linkages between users.
- **`notifications`**: In-app alerts, email triggers, and push notification aggregators.
- **`posts`**: User-generated content feed, status updates, and media sharing.
