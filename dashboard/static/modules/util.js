// Pure helpers — no DOM mutation, no module-level state.
// Extracted from app.js as part of Phase D.

export function stripAnsi(str) {
    return str.replace(/\x1b\[[0-9;]*m/g, "");
}

export function classifyLogLine(text) {
    if (text.includes("[PIPELINE]")) return "log-line-pipeline";
    if (text.includes("[WARN]")) return "log-line-warn";
    if (text.includes("[ERROR]")) return "log-line-error";
    if (text.includes("[INFO]")) return "log-line-info";
    return "";
}

export function parseStageNumber(stageText) {
    const match = stageText.match(/Stage (\d+)\/8/);
    return match ? parseInt(match[1]) : 0;
}

export function escAttr(str) {
    return String(str).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

export function humanBytes(n) {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${units[i]}`;
}

export async function apiRequest(url, method, body) {
    const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { error: text.substring(0, 200) }; }
    return { ok: res.ok, data };
}

export async function apiPost(url, body) {
    return apiRequest(url, "POST", body);
}
