"""Buffer Clip Poster — a small, separate Flask app for batch-publishing
finished clips to TikTok + Instagram Reels through the Buffer.io GraphQL API.

Deliberately independent from the dashboard package (owner directive
2026-07-16: "similar but separate ... start it up like the dashboard but on a
different port"). It shares the same on-disk world (config/paths.json for the
clips folder) but imports nothing from dashboard/ and runs on its own port
(default 5100; pin with POSTER_PORT).

Modules:
    _state.py         — paths, API-key/config loading, batch-job globals
    buffer_client.py  — Buffer GraphQL client (channels, createPost)
    media_host.py     — Cloudinary signed upload (Buffer has NO upload
                        endpoint; media must live at a public HTTPS URL)
    worker.py         — background batch-post thread (bounded, cancellable)
    routes.py         — the Flask blueprint (all /api/* endpoints)
    app.py            — entrypoint
"""
