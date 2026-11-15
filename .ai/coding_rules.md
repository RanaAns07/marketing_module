# Django Project Coding Rules

This document outlines the strict engineering rules and standards that must be adhered to when reviewing, refactoring, or writing code for this repository. All AI assistants MUST follow these rules.

## 1. Django Best Practices
- **Fat Models, Thin Views** (or alternatively, **Fat Services, Thin Views**): Business logic must reside in service classes (or models when appropriate), not in views or serializers. Views should only handle HTTP logic (parsing request, delegating to a service, returning formatted response).
- **Environment Driven**: Do not hardcode secrets, URIs, or credentials. Rely on `.venv` and environment variables.
- **RESTful APIs**: Use standard HTTP methods and standard response codes for DRF. Keep URLs standard and predictable.

## 2. ORM Optimization Rules
- **No N+1 Queries**: This is a cardinal rule. Always inspect queries to ensure relationships are loaded efficiently.
- **`select_related`**: Always use `select_related()` for `ForeignKey` and `OneToOneField` relationships when you know you will access the related object.
- **`prefetch_related`**: Always use `prefetch_related()` for `ManyToManyField` and reverse `ForeignKey` relationships when they are accessed in loops or sequences.
- **`only` and `defer`**: Limit selected fields to what is essential if the table has large text fields or a huge amount of columns, but use with caution to avoid deferred loading N+1s.
- **Bulk Operations**: Prefer `bulk_create`, `bulk_update`, and `.update()` directives over iterating and saving individually. For example, never loop over models and call `.save()` unless absolutely necessary for signals.
- **Aggregation**: Utilize database-level aggregations (`Sum`, `Count`, `Avg`, etc.) rather than computing totals within Python.

## 3. STRICT QUERY DISCIPLINE

**Requirements:**
- ALL complex queries MUST exist in `selectors.py`
- Views MUST NOT construct complex querysets
- Serializers MUST NOT trigger database queries
- Services MAY call selectors but must not contain heavy ORM logic
- Any query involving joins, annotations, or aggregations MUST be isolated

*This query discipline section is strict and non-negotiable. Any violation is considered an architectural failure.*

## 4. Clean Code and Maintainability
- **Type Hinting**: Use modern Python type hints (`List`, `Dict`, `Optional`, and built-in generics) for all new functions, methods, and service layers.
- **Single Responsibility Principle**: Functions and classes should do one thing. If a view handles parsing out files, executing payment, and sending emails, refactor it into distinct service functions.
- **Error Handling**: Use custom descriptive exception classes or standard DRF exceptions correctly. Do not shadow base exceptions without logging them.
- **Variable Naming**: Ensure variables strictly describe the content. Use `is_xxx` for booleans, `xxx_list` or plural for iterables. Do not use generic names like `data` unless absolutely necessary (e.g., standard DRF `.validated_data`).

## 5. Performance and Scalability
- **Pagination**: Any endpoint returning a dynamic list format MUST be paginated (e.g., using `LimitOffsetPagination` or `PageNumberPagination`).
- **Caching**: Utilize memory or Redis caching for expensive datasets and aggregated counts where real-time accuracy is flexible. Use `@method_decorator(cache_page(x))` or customized cache patterns when required.
- **Asynchronous Task Processing**: Any long-running operation (email sending, report generation, complex calculations, image resizing) must be deferred to Celery (or equivalent message queue) and not executed in the synchronous HTTP request/response cycle.
- **Database Indexing**: Ensure any fields commonly queried against (`filter(field=...)`) or ordered by (`order_by('field')`) are indexed.

## 6. TRANSACTION SAFETY RULES
- **Atomic Mutations**: Any service method that mutates multiple rows or models MUST be wrapped in `transaction.atomic()`.
- **Row Locking**: When reading a row for subsequent immediate mutation in a concurrent environment, use `.select_for_update()` to prevent race conditions.
- **Never Wrap Reads**: Do not wrap pure read operations (`selectors`) inside database transactions.

## 7. SERIALIZER PERFORMANCE RULES
- **No DB Queries**: Serializers MUST NOT execute database queries.
- **`SerializerMethodField` Warning**: Never execute a query inside a `SerializerMethodField`. Source the data via annotations or prefetching beforehand.
- **Read-Only Fields**: Explicitly mark read-only fields as `read_only=True` to bypass unnecessary validation logic during write operations.

## 8. LOGGING AND OBSERVABILITY RULES
- **Audit Trails**: All critical state mutations within services MUST emit structured logs (e.g., `logger.info("payment_processed", user_id=xxx, amount=yyy)`).
- **Error Capturing**: Base exceptions in `except` blocks must be logged via `logger.exception()` before raising custom API errors. Do not swallow errors silently.
- **Context Injection**: Use contextual logging where possible to trace requests (e.g., injecting request correlations/user context).
