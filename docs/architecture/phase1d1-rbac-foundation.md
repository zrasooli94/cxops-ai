# Phase 1D.1 — RBAC Foundation & Organization Membership Roles

This document describes the RBAC foundation established in Phase 1D.1: the
role model, the capability matrix, the centralized authorization context, and
the resolution chain that turns identity → tenant → role → capabilities.

Phase 1D.1 deliberately ships **no** member-management application routes, no
mass-edit endpoints, and no fine-grained agent-tool policy. Those arrive in
later 1D phases (1D.2, 1D.4). The foundation here is the only place where
role strings are translated into capabilities.

---

## Identity → Tenant → Role → Capability

The authorization chain composes three already-resolved stages, each with a
single source of truth:

| Question                | Resolved by                     | Produces               | Security-critical invariant |
|-------------------------|---------------------------------|------------------------|-----------------------------|
| "Who are you?"          | Authentication (Phase 1B)       | `AuthenticatedPrincipal` (subject) | JWT signature/issuer/audience must verify |
| "Which organization?"   | Tenant resolution (Phase 1C.1)  | `TenantContext` (organization_id + subject) | membership bound to `subject` only; selector never authorizes |
| "What role do you hold there?" | Authorization (Phase 1D.1) | `AuthorizationContext` (organization_id + subject + role) | **DB membership row is the only role source** |

Role resolution is deliberately **re-verified**: `resolve_authorization_context`
queries `organization_memberships` again with `(subject, organization_id)` even
though `TenantContext` was already validated. This protects against a race
where a membership is revoked between tenant resolution and authorization
resolution — the authorization call fails closed with 403.

---

## Roles

`app/core/rbac.py::OrganizationRole` — stable, organization-scoped roles,
persisted as lowercase strings.

| Role         | Blast radius                             |
|--------------|------------------------------------------|
| `owner`      | Full control, including org/member management |
| `admin`      | Full control (same capability surface as owner today) |
| `supervisor` | Operations plus agent approval/execution control |
| `agent`      | Day-to-day customer/ticket handling |
| `viewer`     | Read-only across the org |

These roles are **separate from Nhost/Hasura roles and JWT claims** by design.
A subject's role is discovered solely from the organization membership row.

---

## Capability Matrix

`Capability` values are deterministic strings (`entity.action`). New
capabilities are added in `app/core/rbac.py`; services must never scatter raw
strings.

| Capability                          | owner | admin | supervisor | agent | viewer |
|-------------------------------------|-------|-------|------------|-------|--------|
| `organization.read`                 | ✓     | ✓     |            |       |        |
| `organization.manage`               | ✓     | ✓     |            |       |        |
| `member.read`                       | ✓     | ✓     | ✓          |       |        |
| `member.manage`                     | ✓     | ✓     |            |       |        |
| `customer.read`                     | ✓     | ✓     | ✓          | ✓     | ✓      |
| `customer.write`                    | ✓     | ✓     | ✓          |       |        |
| `ticket.read`                       | ✓     | ✓     | ✓          | ✓     | ✓      |
| `ticket.write`                      | ✓     | ✓     | ✓          | ✓     |        |
| `agent.run`                         | ✓     | ✓     | ✓          | ✓     |        |
| `agent.approve`                     | ✓     | ✓     | ✓          |       |        |
| `agent.execute`                     | ✓     | ✓     | ✓          |       |        |
| `knowledge.read`                    | ✓     | ✓     | ✓          | ✓     | ✓      |
| `knowledge.manage`                  | ✓     | ✓     | ✓          |       |        |
| `automation.read`                   | ✓     | ✓     | ✓          |       | ✓      |
| `automation.manage`                 | ✓     | ✓     |            |       |        |
| `integration.read`                  | ✓     | ✓     | ✓          | ✓     | ✓      |
| `integration.manage`                | ✓     | ✓     |            |       |        |
| `observability.read`                | ✓     | ✓     | ✓          |       | ✓      |

`ROLE_CAPABILITIES` is the **only** place role strings are translated into
capabilities. `capabilities_for_role` is fail-closed: an unknown role gets
`frozenset()`.

---

## Authorization Context

`app/core/rbac.py::AuthorizationContext` — a frozen dataclass:
`organization_id`, `subject`, `role`. It derives `.capabilities` from the role
matrix and exposes `has(capability)` / `require(capability)`.

Helper functions used by services and dependencies:

- `has_capability(authz, capability)` — fail-closed boolean check (`None`
  authz or capability → False).
- `require_capability(authz, capability)` — raises `MissingCapabilityError`.

`app/services/authorization_service.py::resolve_authorization_context(db,
principal, tenant)` resolves role from the DB membership row and raises:

| Error                             | Meaning                                            | HTTP |
|-----------------------------------|----------------------------------------------------|------|
| `AuthorizationMembershipMissingError` | membership revoked after tenant resolution | 403 |
| `AuthorizationRoleInvalidError`   | persisted role value is not a valid `OrganizationRole` | 403 |

Both are fail-closed (deny). Unknown roles and revoked memberships are never
silently downgraded to read-only; they are denied outright.

---

## Database Schema

`organization_memberships.role` — `varchar(20)`, NOT NULL, with
`CHECK (role IN ('owner','admin','supervisor','agent','viewer'))`.

Migration `1d100001_add_membership_roles` backfills pre-RBAC rows
deterministically (no privilege escalation):

- 1 membership in an org → that member becomes `owner` (the org was created by
  them pre-RBAC).
- Multiple memberships in an org → all become `viewer` (no evidence of who
  should be owner; a future owner can promote them in Phase 1D.2/1D.4).

The DB CHECK constraint is a second line of defense: even a bug in the app
cannot persist an unmodeled role. `scripts/bootstrap_tenant.py` now requires an
explicit `--role`, so bootstrap tooling can never silently escalate.

---

## FastAPI Dependencies

`app/api/deps.py`:

- `CurrentAuthorization` — resolves `AuthorizationContext` from
  `CurrentPrincipal` + `CurrentTenant`, mapping authorization resolution errors
  to 403.
- `RequireCapability(capability)` — dependency factory producing a
  `CurrentAuthorization`-shaped dependency that denies (403) when the required
  capability is missing.

```python
async def create_ticket(
    authz: Annotated[AuthorizationContext, Depends(RequireCapability("ticket.write"))],
    ...
):
```

Proof endpoint wired this phase to keep the blast radius tiny:
`GET /me/authorization` (`app/api/routes/tenant.py`) returns only safe data —
`{organization_id, role, capabilities}`. It never echoes subject, token, or
JWT claims.

---

## Role Spoofing Resistance

No request input can influence the resolved role:

| Attack vector             | Why it fails                                      |
|---------------------------|---------------------------------------------------|
| `X-CXOps-Role` header     | header is not read anywhere in authorization      |
| JWT `role` claim          | claims are never consulted; DB membership is truth |
| request body `role` field | `OrganizationCreate` has no role field; the creator membership is created with an explicit `owner` role |
| forged `X-CXOps-Organization-ID` | selector is correlated with `subject`; a selector for another org → 403 |
| missing selector with many memberships | → 409, never a random pick |

---

## Tests

`tests/test_rbac.py` — deterministic, synthetic subjects (`user-alpha`,
`user-beta`), clean up every organization they create. Covers:

- role enum values and capability matrix (owner/admin/supervisor/agent/viewer)
- unknown role fails closed (`frozenset()`, no capability)
- `has_capability` / `require_capability` semantics
- creator receives `owner` explicitly; repository `create` requires an explicit role
- DB CHECK constraint rejects invalid roles (`IntegrityError`)
- bootstrap honors `--role`
- authorization resolves per-membership; multi-org role switching
- membership revoked after tenant resolution fails closed
- invalid persisted role raises `AuthorizationRoleInvalidError`
- proof endpoint: 401 unauthenticated, 200 with safe data, viewer capability set
- role spoofing via header, JWT claim, and request payload is ignored
- forged organization selector → 403; ambiguous selection → 409
- `RequireCapability` dependency denies/allows

---

## Out of Scope (deferred)

- Member-management application routes (add/remove/promote members) → **Phase 1D.2**
- Mass-edit / admin console endpoints → **Phase 1D.4**
- Fine-grained agent-tool policy → later 1D phase
- Frontend authorization (hiding/disable UI by role) — API is authoritative now