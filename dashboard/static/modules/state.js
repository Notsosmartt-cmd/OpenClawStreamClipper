// Shared mutable state for the dashboard. Modules import this and read/write
// the named exports. Re-export pattern means rebinding works across imports.
// Extracted from app.js as part of Phase D.

export const state = {
    selectedVod: null,
    pipelineRunning: false,
    evtSource: null,
};
