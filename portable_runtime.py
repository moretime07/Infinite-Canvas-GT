"""Local-only listener settings for the optional Windows portable launcher."""


def listen_options(environ):
    if environ.get("INFINITE_CANVAS_PORTABLE") != "1":
        return "0.0.0.0", 3000
    # The portable distribution is personal by default: don't expose coworkers'
    # keys and canvases to the LAN merely because they double-click an EXE.
    host = "127.0.0.1"
    try:
        port = int(environ.get("INFINITE_CANVAS_PORT", "3000"))
    except ValueError:
        raise ValueError("Portable server port must be an integer") from None
    if not 1024 <= port <= 65535:
        raise ValueError("Portable server port must be between 1024 and 65535")
    return host, port
