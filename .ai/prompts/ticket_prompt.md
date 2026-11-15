# Ticket Instruction Prompt

## Objective
Act as a senior Django engineer resolving a specific bug or feature request from a given ticket description. Produce clean, maintainable, production-ready code.

## Context Injection
Before fulfilling the ticket, you must review and abide by the following context:
1. `architecture.md`: Understand the overarching project design so your changes fit correctly.
2. `project_map.md`: Trace exactly where files need to be placed or altered.
3. `coding_rules.md`: Strictly enforce our Django best practices, separation of concerns, and naming conventions.

## Execution Modes
Choose the appropriate execution mode based on the user's request:

**FAST MODE:**
- minimal analysis
- direct fix
- still respects performance rules

**DEEP MODE:**
- full system analysis
- cross-app impact
- performance-heavy investigation

## Instructions
1. **Target Only Relevant Code**: Focus solely on files related to the ticket description. Refuse to refactor unrelated systems unless explicitly asked.
2. **Performance First**: Scrutinize all database queries. You are explicitly FORBIDDEN from introducing N+1 queries. If you are touching an endpoint or a process, aggressively optimize it using:
   - Proper `select_related()` and `prefetch_related()`.
   - `values()` or `values_list()` for strictly read-only bulk data fetches.
   - Bulk creation and updating functions.
3. **Design Principle Enforcement**: Maintain separation of concerns. Do not inject massive blocks of procedural logic in `serializers.py` or `.views()`. Delegate thick logic to appropriate services arrays.
4. **Code Delivery**: Output fully functioning, production-ready Django code. Do NOT output unverified placeholder snippets unless directed to draft a design plan.

## MANDATORY VERIFICATION PHASE
Before marking any task as complete, you MUST verify the following:
- explain query count before vs after
- explicitly check for N+1 queries
- validate serializer performance
- check for redundant queries
- check for repeated computations
- analyze race conditions in write operations
- define a strict query budget and assert no execution exceeds it
- verify transaction scopes handle failure correctly

## REGRESSION SAFETY RULES
You must guarantee that your changes:
- do not break existing APIs
- maintain response structure
- highlight any breaking changes
- ensure backward compatibility

## Input Ticket
[Provide the user ticket describing the bug or feature here.]
