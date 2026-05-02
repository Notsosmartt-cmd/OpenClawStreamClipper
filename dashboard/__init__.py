"""Stream Clipper dashboard package.

Modularized in Phase C of the modularization plan
(see AIclippingPipelineVault/wiki/concepts/modularization-plan.md).

Layout:
    _state.py            — shared mutable state (paths, pipeline_process, defaults)
    config_io.py         — load/save helpers for config/*.json
    pipeline_runner.py   — DetachedDockerPipeline + spawn / poll / kill
    routes/              — Flask blueprints, one per URL domain
    app.py               — bootstrap + blueprint registration
"""
