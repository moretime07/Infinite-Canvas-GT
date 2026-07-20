# Task 4 no-compatible-provider fix report

## Root cause

When configured providers existed but none supported chat or video, `chatApiProviders()` and `videoApiProviders()` replaced the empty compatible result with `defaultApiProviders()`. New default-mode LLM/video nodes then selected fallback models from generic model lists even though `preferredProviderId()` correctly returned an empty provider ID.

## RED evidence

Command:

```powershell
node tests/canvas-follow-default-provider.test.js
```

Before the production fix, the new production-path assertion failed with:

```text
chatProviders: ['chat-only', 'openrouter', 'fallback']
videoProviders: ['chat-only', 'openrouter', 'fallback']
llm: { provider: '', model: 'chat-a' }
video: { provider: '', model: 'fabricated-video-model' }
```

Expected all provider lists, provider IDs, and model IDs to be empty. A second test-first assertion then failed because an unresolved default LLM rendered `fabricated-chat-model` instead of the existing disabled no-model option.

## Minimal implementation

- Preserve empty compatible results from `chatApiProviders()` and `videoApiProviders()`.
- Give new default-mode LLM/video nodes an empty model when no compatible provider resolves.
- Pass the node to `chatModelOptions()` so only an unresolved explicit default-mode LLM uses the existing no-model hint; fixed and missing-mode legacy behavior keeps the prior fallback path.

## GREEN evidence

The following commands all exited 0:

```powershell
node tests/canvas-follow-default-provider.test.js
node tests/canvas-provider-defaults.test.js
node tests/provider-defaults.test.js
node tests/smart-canvas-provider-defaults.test.js
node tests/canvas-output-auto-export.test.js
node --check static/js/canvas.js
node --check static/js/canvas-provider-mode.js
node --check static/js/provider-defaults.js
node --check tests/canvas-follow-default-provider.test.js
git diff --check
```

No server was started, no model/API request was made, and no runtime data or secrets were touched.
