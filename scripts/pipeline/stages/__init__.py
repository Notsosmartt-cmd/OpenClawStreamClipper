"""Stage modules — one per pipeline stage. Each exposes ``run(ctx)`` and
mutates the shared :class:`run_pipeline.Ctx`. Ported 1:1 from
``scripts/stages/stageN.sh``; the heavy work still lives in the reused
``scripts/lib/**`` modules invoked via ``common.run_module``.
"""
