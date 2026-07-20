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

## Review follow-up: request guards and provider hints

### RED evidence

The provider-control test executed `chatProviderOptions()` and `videoProviderOptions()` with configured providers that had no compatible chat/video models. The focused test failed first with the actual LLM markup:

```text
<option value="__default__" selected>Follow default API（暂无）</option>
```

It was missing the expected disabled `<option value="">No API providers</option>` hint. Video used the same incomplete branch.

After the hint fix was GREEN, request-path tests executed the production `runGenerator`, `runGeneratorLegacy`, `runVideoNode`, and `callCanvasLLM` functions with valid prompts, explicit `providerMode:'default'`, an all-incompatible configured provider set, and spies at the real fetch/cascade/image-task request boundaries. Before the request guard, the focused test failed with:

```text
AssertionError: generator must stop before fetch/request helpers
1 !== 0
```

All four helpers had already been executed and recorded an attempted request. This proved the empty resolved fields were not sufficient to prevent request-time fallbacks or network work.

### Minimal implementation

- Chat/video provider controls keep the selected follow-default option and append the existing disabled no-provider hint when their compatible provider list is empty.
- A shared explicit-default guard checks the real provider field and model after synchronization.
- Image (current and legacy), video, and LLM request paths stop before payload construction or any request helper. Normal runs surface the existing API error UI; cascade/direct calls throw the same clear error.
- The guard applies only to `providerMode:'default'`. Focused tests execute fixed LLM and missing-mode legacy video paths and confirm they still reach their existing request boundary with original provider/model fields unchanged.

### GREEN evidence

The focused test passed with zero request calls and the exact existing no-provider error for all four unresolved explicit-default paths. It also covered the no-model error branch and fixed/legacy non-regression.

The full regression matrix then passed:

```powershell
E:\claude\Infinite-Canvas-GT-main\.venv\Scripts\python.exe -m unittest tests.test_primary_provider -v
E:\claude\Infinite-Canvas-GT-main\.venv\Scripts\python.exe -m unittest tests.test_openrouter_image_generation -v
node tests/canvas-follow-default-provider.test.js
node tests/api-settings-primary-provider.test.js
node tests/api-settings-default-openrouter.test.js
node tests/provider-defaults.test.js
node tests/openrouter-page-defaults.test.js
node tests/online-default-openrouter.test.js
node tests/canvas-provider-defaults.test.js
node tests/smart-canvas-provider-defaults.test.js
node tests/canvas-output-auto-export.test.js
node --check static/js/canvas.js
node --check static/js/canvas-provider-mode.js
node --check static/js/provider-defaults.js
node --check tests/canvas-follow-default-provider.test.js
git diff --check
```

Results: 17 primary-provider Python tests passed, 2 OpenRouter image-generation Python tests passed, all nine JavaScript regression suites passed, all syntax checks exited 0, and `git diff --check` exited 0. The worktree-local `.venv` was absent, so the repository-root virtual environment was used. No server, model call, runtime data, or secret was involved.
