// Stream Clipper Dashboard — entry module.
// Imports each panel module and wires DOM events. Inline `onclick=` handlers
// in HTML reach functions via `window.*`, so we expose those here.
//
// Modularized in Phase D — see AIclippingPipelineVault/wiki/concepts/modularization-plan.md

import {
    fetchOriginality, browseMusicFolder, scanMusicLibrary,
    onOriginalityChange,
    startClip, startClipAll, stopPipeline,
    pollStatus,
} from "./modules/pipeline-ui.js";

import {
    fetchVods, fetchClips, fetchStages, selectVod,
} from "./modules/vods-panel.js";

import {
    fetchModels, onModelChange, resetModel, saveModels,
} from "./modules/models-panel.js";

import {
    fetchHardware, onHardwareDropdown, saveHardware, restartServices,
} from "./modules/hardware-panel.js";

import {
    fetchFolders, browseFolderFor, onFoldersChange, saveFolders,
} from "./modules/folders-panel.js";

import {
    fetchAssets, fetchAsset,
} from "./modules/assets-panel.js";

// Inline onclick= handlers in HTML need these on window.
Object.assign(window, {
    selectVod,
    onModelChange, resetModel,
    onHardwareDropdown,
    onFoldersChange, browseFolderFor, saveFolders,
    onOriginalityChange, browseMusicFolder, scanMusicLibrary,
    fetchAsset,
});

document.addEventListener("DOMContentLoaded", () => {
    fetchVods();
    fetchClips();
    fetchStages();
    fetchModels();
    fetchHardware();
    fetchFolders();
    fetchOriginality();
    fetchAssets();
    pollStatus();
    setInterval(pollStatus, 3000);

    document.getElementById("btn-clip").addEventListener("click", startClip);
    document.getElementById("btn-clip-all").addEventListener("click", startClipAll);
    document.getElementById("btn-stop").addEventListener("click", stopPipeline);
    document.getElementById("btn-refresh-clips").addEventListener("click", fetchClips);
    document.getElementById("btn-refresh-vods").addEventListener("click", fetchVods);
    document.getElementById("btn-refresh-models").addEventListener("click", fetchModels);
    document.getElementById("btn-save-models").addEventListener("click", saveModels);
    document.getElementById("btn-refresh-hardware").addEventListener("click", fetchHardware);
    document.getElementById("btn-save-hardware").addEventListener("click", saveHardware);
    document.getElementById("btn-restart-services").addEventListener("click", restartServices);
    document.getElementById("btn-refresh-folders").addEventListener("click", fetchFolders);
});
