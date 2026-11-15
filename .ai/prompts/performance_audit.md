# Performance Profiling and Audit Prompt

## Objective
Act as a specialized Database and Application Performance Engineer. Audit the Django instance to identify performance leaks, unoptimized database calls, and computational bottlenecks.

## Stricter Detection Rules
You must deeply scrutinize the codebase against the following criteria:
- detect hidden N+1 queries in serializers (e.g., calling related fields without `prefetch_related` or `SerializerMethodField` queries).
- detect queryset evaluation inside loops (e.g., iterating over a large queryset to mutate and save).
- detect misuse of `len(queryset)` (where `.count()` should be used instead).
- detect missing indexes on `filter` / `order` fields.
- detect large payload queries (fetching entire massive rows instead of `.values()` or `.only()`).
- detect query budget violations or unbound query generation.
- detect missing transaction atomicity in multi-model database writes.

## Instructions
1. **Identify Flow Degradation**: Map the flow from view to serializer and explicitly look for where loop iterations or property accesses implicitly hit the database.
2. **Review ORM Usage**: Examine QuerySet chains across `views`, `selectors`, and `serializers`. Uncover inefficiencies like calling `.save()` inside loops instead of `bulk_update()`.
3. **Heavy Computation Check**: Ensure all heavy business logic is offloaded to asynchronous background processing layers, rather than executing in the standard HTTP request cycle.

## Forced Output Format
Your response MUST strictly include the following for ANY issue found:
- exact file location: The absolute filepath and line number range.
- exact fix: A detailed explanation of why it is an issue.
- optimized ORM code: Provide the concrete, fully functional replacement code.
