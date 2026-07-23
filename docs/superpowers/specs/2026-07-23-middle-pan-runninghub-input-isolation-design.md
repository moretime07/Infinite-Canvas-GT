# Middle-button canvas pan and RunningHub input isolation

## Goal

Make desktop canvas navigation predictable and prevent RunningHub AI-app template images from contaminating generated videos.

## Scope

This change covers two behaviors in the classic infinite canvas:

1. The canvas viewport pans only while the middle mouse button is held.
2. RunningHub receives only media explicitly connected on the canvas; unused optional image slots must not fall back to images saved in the RunningHub web app.

Touch gestures, mouse-wheel zoom, node dragging, link creation, minimap navigation, knife mode, and existing selection shortcuts remain unchanged.

## Canvas interaction design

- Middle-button drag starts viewport panning from any non-editable canvas surface.
- Releasing the middle button ends panning.
- A defensive mouse-move check ends panning if the browser reports that the middle button is no longer pressed. This prevents a missed mouse-up event from leaving the viewport attached to the pointer.
- Left-clicking empty canvas space clears the current selection.
- Left-dragging empty canvas space does not pan.
- Ctrl/Cmd-drag and the existing `R` selection mode continue to create a selection rectangle.
- Left-button interactions on nodes, ports, resize handles, menus, and editable controls retain their current behavior.

## RunningHub media isolation design

RunningHub AI-app schemas may contain default image filenames saved by the original web app. Omitting an optional image field from `nodeInfoList` allows the app to reuse that saved image.

The canvas will build image fields in schema order:

- Connected media is uploaded and assigned to the corresponding image slot.
- A required image slot without connected media stops before task submission and shows a validation error.
- An unused optional image slot whose schema explicitly supports the `None` sentinel is submitted with `fieldValue: "None"`.
- If an unused optional slot does not advertise a supported empty sentinel, it remains omitted; the code must not invent an unsupported value.
- Saved template filenames must never be submitted as fallbacks for unconnected image slots.

This behavior applies to RunningHub AI-app nodes. Existing workflow-mode pruning remains unchanged because workflow JSON fields are removed structurally there.

## Data flow

1. Read connected media from the canvas and preserve connection order.
2. Match media to RunningHub image fields using the existing field-index mapping.
3. Upload only connected media.
4. Emit uploaded filenames for connected slots and `None` for supported unused optional slots.
5. Validate all required slots before calling the RunningHub submit endpoint.
6. Submit the resulting `nodeInfoList`.

## Error handling

- Missing required media produces a local validation error and no paid submission.
- Upload failures keep the current retry and diagnostic behavior.
- Unsupported empty-slot schemas do not receive guessed values.
- The prompt and non-media RunningHub parameters are unaffected.

## Tests

Automated tests must verify:

- Left-button blank-space drag cannot start viewport panning.
- Middle-button drag can start panning.
- Panning stops when the middle-button bit is absent during mouse movement.
- Left-click blank space still clears selection.
- An optional image field that supports `None` receives `fieldValue: "None"` when unconnected.
- Connected images are uploaded and assigned in order.
- Required missing images fail before submission.
- Template image filenames are not used for unconnected slots.
- Existing Python and JavaScript regression suites remain green.
