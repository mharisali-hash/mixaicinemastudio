---
title: "Contract v42: Rights and moderation"
status: Active
created: 2026-11-08
extends: contract-v27 (tokens), contract-v38/v39 (accounts, packages, admin), contract-v41 (Print Studio)
migration: 0034_rights_moderation
---

# Contract v42: Rights and moderation

Two things: who may use which studio and which model (rights), and a **Manager** staff role that looks over what
people make, removes what breaks the rules, warns, and puts accounts on hold (moderation). Everything is decided
and enforced on the server; the app only hides what it's told isn't there.

## 1. Rights

`app.rights.effective(db, user)` gives one answer. The first layer with an opinion wins:

1. **Admin**: everything.
2. **Per-user override** (`user.rights = {"features": {key: bool}, "models": {id: bool}}`), set by an admin. It can
   switch on something nobody else has yet, or switch off something the plan has.
3. **Not released**: a studio or model with no `right_release` row is off for everyone but admins. This is deny by
   default: whatever is added later waits for an admin to release it.
4. **Package**: `package.features[key] = false` switches a studio off; `package.models` is an allow-list (null = every
   released model).

Licence gating (`*_ALLOW_NONCOMMERCIAL`) still sits under all of this and never shows in the app.

**Studios** (`packages.FEATURES`): `image, video, audio, character, ads, poetry, podcast, dub, stories, 3d, api,
song_voices, presenter, print, implant, bulk_edit`. `implant` follows `user.implant_access` (contract M22), not the
layers above.

**Release baseline.** On the first start after migration 0034 every studio that existed before, and every model in
the catalogue, is released, so nobody loses anything. `print`, `presenter` and `bulk_edit` start unreleased
(admins only), as Print Studio was before.

### Where it's enforced

| Where | What happens |
|---|---|
| Every new top-level job (`quota.check_job`, all channels) | 403 for a studio or model the person may not use; `api` for token and MCP jobs |
| Studio routers (podcast, poetry, ads, dub, stories, 3D) | 403 on changes (POST/PUT/PATCH/DELETE); reading and downloading what you made still works |
| `/api/print/*` | 404 unless `print` is allowed (it was admin-only) |
| `GET /api/models`, Ad and Print model pickers | models the person may not use are left out; `auto` stays |
| Auto (`rights.choose`) | picks only from allowed models; asking for a disallowed one is a 403 before anything is queued |
| MCP | `tools/list` leaves out tools of a switched-off studio; `tools/call` answers "Unknown tool" |
| Poetry Short with a children's voice | 403 without `song_voices` |

`GET /api/account/usage` returns the resolved `features` (every key) and `models` (allowed ids; null for admins).

### Admin endpoints (session only, 404 for everyone else)

| Method and path | Body | |
|---|---|---|
| `GET /api/admin/rights` | | `{features: [{key,label,released,own_switch}], models: [{key,label,type,released}], packages: [{id,name,kind,custom,features,models}]}` |
| `POST /api/admin/rights/release` | `{kind: "feature"\|"model", key}` | opens it up to packages |
| `POST /api/admin/rights/withdraw` | same | back to admins only |
| `GET /api/admin/users/{id}/rights` | | overrides, resolved features and models, staff role, scope, team |
| `PUT /api/admin/users/{id}/rights` | `{features: {key: true\|false\|null}, models: {id: true\|false\|null}}` | null (or leaving a key out) follows the plan; replaces the whole set |
| `PUT /api/admin/users/{id}/staff` | `{staff_role: "manager"\|null, manager_scope: "site"\|"team", team: [user ids]}` | |

## 2. Staff roles

`GET /api/auth/me` gains `staff: "admin" | "manager" | null` and `blocked: {reason, at} | null`.

- **Admin** (`user.is_admin`): everything below, plus rights, packages, billing and staff roles.
- **Manager** (`user.staff_role = "manager"`): the moderation endpoints only. `manager_scope = "site"` covers every
  account that isn't staff; `"team"` covers the users in `manager_member`. A manager never reaches an admin,
  another manager, or anything under `/api/admin` (404). Managers are not unlimited: their own work goes through
  their package like anyone's.

**Admin work is private.** Whatever an admin makes never appears to anyone else, managers and other admins included (owner, 8 Nov): every moderation list, file and action leaves admin-owned items out.

Implant Preview photos and results are never MediaItems, so no moderation endpoint can list or serve them.

## 3. Moderation (`/api/moderation`, admins and managers, session only, 404 for everyone else)

| Method and path | Body / query | |
|---|---|---|
| `GET /items` | `user, kind (image\|video\|audio), since, until, before, state (queue\|reviewed\|removed\|all), q, limit` | `{items: [...], next}`; `next` is the `before` for the next page. Queue = generated, not reviewed, not removed. Admins' items never appear |
| `POST /items/reviewed` | `{ids}` | marks them fine; out of the queue |
| `POST /items/{id}/remove` | `{reason, warn, email}` | soft removal (below); the owner gets a notice, which counts as a warning when `warn` |
| `POST /items/{id}/restore` | | puts it back |
| `GET /items/{id}/thumb`, `/file` | | the picture, across workspaces, for the review screen |
| `GET /users` | `q, status (all\|warned\|blocked)` | `{warn_limit, users: [{id,email,name,is_active,warnings,blocked_at,blocked_reason}]}`; warnings count for 90 days |
| `POST /users/{id}/warn` | `{reason, item_id?, email}` | 201 |
| `POST /users/{id}/block` | `{reason, email}` | puts the account on hold |
| `POST /users/{id}/unblock` | `{reason?, email}` | lifts it |
| `GET /log` | `limit` | moderation audit rows; a manager sees those about people in their reach and their own |

Item: `{id, kind, origin, title, prompt, media_type, width, height, duration_s, created_at, owner: {id,email,name},
reviewed_at, removed_at, removed_reason, thumb_url, file_url}`.

**Removed** (`media_item.removed_at`): gone for its owner, through every channel. It is out of every list (via
`visibility.where`), its detail and edit routes answer 404, and its files answer 404 (`/api/media/...`, thumbs and
signed links). Nothing is deleted, and a restore brings it all back.

**On hold** (`user.blocked_at`, with a reason): the person can still sign in, look around and download. Every other
change is refused with 403 "Your account is on hold: ‹reason›". The exceptions are signing out and acknowledging
notices.
- Their queued jobs are cancelled, and the worker won't render anything of theirs.
- REST tokens and MCP are refused (403 with the reason). The tokens are not revoked, so they work again after the
  hold is lifted.
- The People screen suggests a hold at `MODERATION_WARN_LIMIT` warnings in 90 days (default 3). It never puts an
  account on hold by itself.

Audit actions: `moderation_remove`, `moderation_restore`, `moderation_reviewed`, `moderation_warning`,
`moderation_block`, `moderation_unblock`, `rights_update`, `right_release`, `right_withdraw`, `staff_update`.
Email (optional per action) goes through `app.mailer`.

### The person's own notices

| | |
|---|---|
| `GET /api/account/notices` | open notices (warning, removal, unblock), newest first; the hold itself is in `/auth/me` |
| `POST /api/account/notices/{id}/ack` | 204; allowed while on hold |

## 4. Data (migration 0034)

- `user`: `staff_role`, `manager_scope` (default "site"), `rights` (JSON, default {}), `blocked_at`, `blocked_reason`,
  `blocked_by`.
- `media_item`: `reviewed_at`, `reviewed_by`, `removed_at`, `removed_by`, `removed_reason`.
- New tables: `manager_member (manager_user_id, user_id)`, `user_notice`, `right_release (kind, key)`.

## 5. App

- **Admin › Rights**: things waiting for release, and grids of studios and models against packages. Each column
  saves through `PATCH /admin/packages/{id}`.
- **User drawer › Rights**: Plan, Allow or Deny for each studio and model, with the result next to it; staff role and
  team.
- **Review, People, Moderation log**: the only admin tabs a manager sees. The rail shows them "Moderation".
- **Banner**: a hold (it can't be dismissed), and the latest notice with "Got it".
- **Gating**: the rail, the menus and the model pickers leave out what the plan lacks. A direct link to a switched-off
  studio shows "This studio isn't part of your plan".
