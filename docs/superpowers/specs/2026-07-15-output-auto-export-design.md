# Output Auto Export Design

## Goal

Allow each Infinite Canvas Output node to automatically save generated images and videos to local Windows folders. Images and videos use separate destinations and independently configurable export formats.

## Defaults

- Image folder: `D:\桌面\1\全能画布图片输出`
- Video folder: `D:\桌面\1\全能画布视频输出`
- Image format: `jpg`
- Video format: `mp4`
- Name template: `{canvas}_{node}_{date}_{index}`

The backend creates a configured folder when it does not exist. A user can replace either folder with any accessible local path in the Output node.

## Node UI

Each Output node gets a compact export control below its media grid:

- An automatic-export toggle, enabled by default for newly created Output nodes.
- A settings control that expands two path inputs and independent image/video format selects.
- A name-template input with the supported variables: `{canvas}`, `{node}`, `{date}`, `{index}`, and `{type}`.
- A manual "export current files" command and a short status message showing the latest result or failure.

The settings are persisted in the Output node so different workflows can use different folders without affecting one another.

## Export Flow

1. A generated media item is appended to an Output node.
2. If automatic export is enabled, the client sends only newly appended items to a local backend endpoint.
3. The backend chooses the image or video folder and output format from the node settings.
4. It resolves local canvas assets directly, or streams remote HTTP(S) output to a temporary file first.
5. Images are converted through Pillow; videos are remuxed or transcoded through the locally available `ffmpeg` executable.
6. The backend writes a sanitized filename, adding a sequence suffix when needed, and returns the saved paths.

The client remembers successfully exported source URLs per Output node to avoid re-exporting prior outputs on a normal re-render or canvas reload. Manual export bypasses that de-duplication and exports all current media.

## Error Handling

- Empty or inaccessible folders return a clear per-node export error without affecting generation results.
- Unsupported media, conversion failure, or missing `ffmpeg` return an explicit error and do not create a misleading renamed file.
- A remote download failure leaves the source item in the Output node and can be retried through manual export.

## Verification

- A backend regression test covers local image export to JPEG, unique filenames, and media-type folder selection.
- A frontend source/runtime check confirms new Output nodes receive the required defaults and schedule auto export only for appended media.
- Compile `main.py`, check `canvas.js`, and exercise the export API with a small local test asset.
