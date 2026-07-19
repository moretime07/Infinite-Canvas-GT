# Default Primary Provider Button Design

**Date:** 2026-07-20

## Goal

Allow the user to choose the application's global primary API provider directly from the API Settings provider list. The change must preserve exactly one primary provider, never expose API keys, and must not overwrite unrelated unsaved form edits.

## Current Behavior and Problem

The provider schema and backend already support a `primary` boolean, and provider selection prefers an enabled primary provider when it supports the requested capability. However, the API Settings page has no direct primary-provider control, and `saveProviders()` currently serializes every provider with `primary:false`. A normal save can therefore erase the configured primary provider.

## User Experience

Each provider card in the API Settings sidebar has a star control in its upper-right corner.

- A non-primary eligible provider shows an outlined star and the label `设为默认`.
- The current primary provider shows a filled star and a `默认` badge. Its star control is disabled.
- Clicking the provider card body continues to open that provider in the editor.
- Clicking the star stops propagation and changes only the primary provider; it does not select the card or save unrelated editor fields.
- The change takes effect immediately without a confirmation dialog.
- While the request is pending, the target control shows a loading state and all primary controls are temporarily disabled.
- On success, the provider cards refresh, a message identifies the new default provider, and the page broadcasts `providers-changed`.
- On failure, the existing primary selection stays visible and the page shows the backend's reason.

Provider cards also show compact capability labels for configured image, chat, and video models. A primary provider remains capability-aware: it is preferred only for capabilities it supports, while existing compatible-provider fallback behavior remains unchanged.

## Eligibility Rules

A provider may become primary only when all of the following are true:

1. The provider exists.
2. The provider is enabled.
3. The provider has a usable configured credential according to its provider-specific key rules.
4. The provider has at least one configured image, chat, or video model.

The UI disables the star when a provider is ineligible and exposes a specific reason such as `平台已停用`, `未配置密钥`, or `未配置模型`. The backend repeats every eligibility check and is authoritative.

The current primary provider cannot be disabled or deleted. The user must first set another eligible provider as primary. This rule prevents an implicit or surprising automatic switch.

## API Design

Add:

```text
PUT /api/providers/{provider_id}/primary
```

The request has no body. In particular, it never accepts or transmits an API key.

The endpoint:

1. Loads the stored providers.
2. Returns `404` when the target ID does not exist.
3. Validates enabled state, provider-specific credential availability, and model availability; invalid targets return `400` with a clear detail message.
4. Builds a replacement provider list in memory, setting the target's `primary` to `true` and every other provider's `primary` to `false`.
5. Saves the complete list once.
6. Returns the public provider records.

The in-memory replacement is prepared before persistence so a validation or save failure does not intentionally alter the previous primary state.

The existing `PUT /api/providers` endpoint remains supported. The API Settings serializer must submit `primary: item.primary === true` instead of hard-coding `false`. The backend must reject attempts through this existing endpoint to disable or remove the current primary provider unless another eligible provider is explicitly marked as the replacement primary in the same request.

## Frontend Components

### Provider-card primary control

`renderProviderList()` renders a primary control for built-in and custom provider cards through one shared helper. The helper derives:

- current-primary state;
- eligibility and disabled reason;
- icon, label, and accessible title;
- pending state.

The control calls `setPrimaryProvider(event, providerId)` and stops card-click propagation.

### Immediate switch action

`setPrimaryProvider`:

1. Stops propagation and ignores duplicate or concurrent requests.
2. Re-checks frontend eligibility for immediate feedback.
3. Calls the dedicated endpoint with an empty `PUT` request.
4. Replaces the local `providers` array only after a successful response.
5. Keeps `selectedId` stable when possible.
6. Re-renders the editor and provider list.
7. Shows success feedback and broadcasts `providers-changed`.
8. Clears pending state in `finally`.

### Existing edit actions

Attempts to disable or delete the current primary provider are blocked before a save request and show `请先设置另一个默认供应商`. The backend enforces the same invariant for direct callers.

## Provider-Specific Credential Rules

The eligibility check uses existing public credential flags instead of key values:

- Standard OpenAI-compatible, ModelScope, Lingjing, and custom providers: `has_key`.
- RunningHub: a credential flag accepted by its existing runnable workflow rules; the implementation must reuse the project's current key-resolution semantics rather than invent a new one.
- Volcengine: the configured credential set required by its existing API workflow.
- Any future provider: its normal public `has_key` signal unless it defines a provider-specific rule.

Frontend checks are informational. Backend key-resolution helpers determine the final result.

## Error Handling

- `404`: provider not found.
- `400`: disabled provider, missing required credentials, no configured models, or an attempt to remove/disable the current primary without a valid replacement.
- `500`: persistence failure; the response provides a safe generic message and does not expose filesystem paths or secrets.
- Network failures leave the current UI state unchanged and allow retry.

## Security and Data Integrity

- The dedicated endpoint receives only the provider ID in the URL.
- No API key or preview is sent by the primary-switch action.
- Responses remain public provider records with masked credential state only.
- The backend guarantees at most one primary provider.
- Normal provider saves preserve the selected primary provider.
- Existing saved provider choices on pages and canvas nodes remain authoritative; this feature changes the global fallback only.

## Testing

Backend tests cover:

- switching to an eligible provider produces exactly one primary provider;
- unknown, disabled, unkeyed, and model-less providers are rejected;
- provider-specific credential rules;
- the prior primary remains unchanged on validation failure;
- normal provider saves preserve `primary`;
- disabling or deleting the current primary is rejected without an eligible replacement;
- no secret fields are required or returned by the dedicated endpoint.

Frontend tests cover:

- primary and non-primary card rendering;
- disabled reasons for ineligible providers;
- star-click propagation is stopped;
- pending state prevents concurrent requests;
- success updates providers, preserves editor selection, broadcasts the change, and shows feedback;
- failure leaves local state unchanged;
- ordinary saves serialize the real `primary` value;
- capability labels accurately reflect configured model lists.

Regression tests verify that user-selected primary providers outrank OpenRouter when compatible, while capability-based fallback continues to choose another compatible provider when the primary lacks the requested capability.

Manual verification switches between configured providers but does not click Generate, Send, Run, or any other paid model action.

## Out of Scope

- Separate primary providers for image, chat, and video.
- Per-canvas or per-conversation global-default overrides.
- Automatic switching when the current primary is disabled or deleted.
- Credential editing through the primary-provider endpoint.
