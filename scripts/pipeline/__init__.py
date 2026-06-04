"""Native (bare-metal Windows) pipeline orchestrator package.

Replaces the bash layer (clip-pipeline.sh + pipeline_common.sh +
stages/stageN.sh) with a pure-Python orchestrator. The heavy lifting still
lives in the reused ``scripts/lib/**`` modules, which this package invokes
as subprocesses via ``common.run_module`` (so they need no edits beyond the
Phase 1 path parameterization).
"""
