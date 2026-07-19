# OpenRouter Online Image Default Design

## Goal

Make the Online Image page select the configured OpenRouter provider by default when the page initializes.

## Scope

- Change the initial provider ID in `static/online.html` from the legacy `comfly` fallback to `custom-api`, the configured OpenRouter provider.
- Keep the existing provider availability validation. If OpenRouter is disabled, removed, or has no usable image models, the page continues to fall back to the first available image provider.
- Keep the existing model selection behavior: use the first configured image model for the selected provider.
- Keep manual provider and model switching unchanged.

## Data Flow

1. The page initializes with `provider = 'custom-api'`.
2. `/api/config` supplies the configured API providers.
3. `renderProviderControls()` confirms that OpenRouter is available.
4. The model selector uses the first item in OpenRouter's `image_models` list.
5. Generation requests continue sending the selected provider as `provider_id` and the selected model as `model`.

## Failure Handling

No new error path is introduced. Existing fallback logic selects the first available provider if `custom-api` is unavailable.

## Verification

- Load `static/online.html` with the current API configuration.
- Confirm the provider selector defaults to `openrouter`.
- Confirm the model selector defaults to OpenRouter's first configured image model.
- Confirm the generated request state uses `provider_id = 'custom-api'`.
- Confirm the working tree contains no secrets or runtime provider configuration.
