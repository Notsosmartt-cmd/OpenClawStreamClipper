// Stream Clipper Dashboard — entry module.
// Imports each panel module and wires DOM events. Inline `onclick=` handlers
// in HTML reach functions via `window.*`, so we expose those here.
//
// Modularized in Phase D — see AIclippingPipelineVault/wiki/concepts/modularization-plan.md

import {
    fetchOriginality, browseMusicFolder, scanMusicLibrary, scanLibraries,
    onOriginalityChange,
    startClip, startClipAll, startNewsCompile, stopPipeline,
    pollStatus,
} from "./modules/pipeline-ui.js";

import {
    fetchVods, fetchClips, fetchStages, toggleVod, toggleAllVods,
} from "./modules/vods-panel.js";

import {
    fetchModels, onModelChange, resetModel, saveModels,
    applyRecommendedContext,
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

import {
    fetchReferenceCorpus, runDecompose, runCards, runOurCards, runDiff,
    stopReferenceJob, loadReport, initReferenceTab,
} from "./modules/reference-panel.js";

// Inline onclick= handlers in HTML need these on window.
Object.assign(window, {
    toggleVod, toggleAllVods,
    onModelChange, resetModel, applyRecommendedContext,
    onHardwareDropdown,
    onFoldersChange, browseFolderFor, saveFolders,
    onOriginalityChange, browseMusicFolder, scanMusicLibrary, scanLibraries,
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
    document.getElementById("btn-news-compile")?.addEventListener("click", startNewsCompile);
    document.getElementById("btn-stop").addEventListener("click", stopPipeline);
    document.getElementById("btn-refresh-clips").addEventListener("click", fetchClips);
    document.getElementById("btn-refresh-vods").addEventListener("click", fetchVods);
    document.getElementById("btn-refresh-models").addEventListener("click", fetchModels);
    document.getElementById("btn-save-models").addEventListener("click", saveModels);
    document.getElementById("btn-refresh-hardware").addEventListener("click", fetchHardware);
    document.getElementById("btn-save-hardware").addEventListener("click", saveHardware);
    document.getElementById("btn-restart-services").addEventListener("click", restartServices);
    document.getElementById("btn-refresh-folders").addEventListener("click", fetchFolders);

    // --- Tab switching (Clipper | Reference Lab) ---
    let referenceLoaded = false;
    function switchView(view) {
        document.querySelectorAll(".tab-btn").forEach(b =>
            b.classList.toggle("active", b.dataset.view === view));
        document.querySelectorAll(".view").forEach(v =>
            v.classList.toggle("active", v.id === `view-${view}`));
        if (view === "reference" && !referenceLoaded) {
            referenceLoaded = true;
            initReferenceTab();
        }
    }
    document.querySelectorAll(".tab-btn").forEach(b =>
        b.addEventListener("click", () => switchView(b.dataset.view)));

    // --- Reference Lab tab controls ---
    document.getElementById("btn-ref-refresh")?.addEventListener("click", fetchReferenceCorpus);
    document.getElementById("btn-ref-decompose")?.addEventListener("click", runDecompose);
    document.getElementById("btn-ref-cards")?.addEventListener("click", runCards);
    document.getElementById("btn-ref-our-cards")?.addEventListener("click", runOurCards);
    document.getElementById("btn-ref-diff")?.addEventListener("click", runDiff);
    document.getElementById("btn-ref-stop")?.addEventListener("click", stopReferenceJob);
});
