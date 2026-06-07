// Shared mutable state for the dashboard. Modules import this and read/write
// the named exports. Re-export pattern means rebinding works across imports.
// Extracted from app.js as part of Phase D.

export const state = {
    // Multi-select: array of VOD stems the user has checked (order = check order).
    selectedVods: [],
    pipelineRunning: false,
    evtSource: null,
};
