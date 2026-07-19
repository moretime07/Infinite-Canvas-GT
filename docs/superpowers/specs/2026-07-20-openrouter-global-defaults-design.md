# OpenRouter Global Defaults Design

## Goal

Use OpenRouter as the default provider for every new generic API workflow while preserving saved user choices and dedicated provider-specific workflows.

## Scope

The change applies to new state in:

- Online Image generation.
- GPT Chat and Agent chat/image provider defaults.
- Infinite Canvas generic API image, LLM, and video nodes.
- Smart Canvas generic API image, LLM, and video settings.
- API Settings initial provider selection.

Existing conversations, canvases, nodes, and valid saved provider selections remain unchanged.

The change does not replace dedicated ModelScope generation, RunningHub, Volcengine, Jimeng, ComfyUI, or provider-specific asset/avatar workflows. Those paths require their own protocols and remain explicit choices.

## Architecture

Add a small shared browser helper at `static/js/provider-defaults.js`. It selects a provider for a requested capability (`image_models`, `chat_models`, or `video_models`) using this order:

1. A valid requested or saved provider, when one exists.
2. An enabled compatible provider marked `primary`.
3. An enabled compatible OpenRouter provider, recognized by its configured base URL or provider name.
4. The first enabled compatible provider.
5. The page's existing legacy fallback when no configured provider supports the capability.

The helper also returns the first configured model when a new workflow has no valid model selection. Pages keep ownership of their UI and saved-state formats; the helper only centralizes provider precedence.

## Component Changes

### Online Image

Replace the page-local hard-coded default with the shared image-provider selection. The visible result remains OpenRouter with its first image model when OpenRouter is configured.

### GPT Chat

When no saved chat or image provider exists, select OpenRouter independently for chat and image capabilities. A valid provider stored in `gpt_chat_settings_v1` continues to win.

### Infinite Canvas

New generic generator, LLM, and video nodes use the shared capability-aware default. Loading or sanitizing an existing node retains its valid provider and only falls back when that provider is missing or incompatible.

### Smart Canvas

New generic API settings use the shared capability-aware default for image, chat, and video. Existing saved canvas settings remain authoritative. Dedicated engine selections remain unchanged.

### API Settings and Runtime Configuration

Keep the existing OpenRouter-first card selection. Mark the current OpenRouter provider as the sole `primary` provider in local runtime configuration without changing or exposing its key. The shared helper still recognizes OpenRouter if the primary marker is absent, so imported configurations remain predictable.

## Error and Fallback Behavior

- Disabled providers are never selected.
- A provider must contain at least one model for the requested capability.
- If OpenRouter lacks a model category, the first compatible provider supplies that category.
- No paid generation or chat request is made during initialization or verification.

## Verification

- Unit-test provider precedence, saved-provider preservation, capability filtering, and fallback behavior.
- Verify Online Image defaults to OpenRouter and its first image model.
- Verify a fresh GPT Chat state defaults chat and image providers to OpenRouter while saved state is preserved.
- Verify new Infinite Canvas image, LLM, and video nodes default to OpenRouter.
- Verify new Smart Canvas generic API settings default to OpenRouter.
- Verify dedicated provider-specific nodes and engines remain unchanged.
- Verify OpenRouter remains visible, enabled, keyed, and marked primary through `/api/config` without printing the key.
- Verify runtime configuration remains ignored by Git.
