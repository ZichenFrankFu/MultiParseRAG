"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {
  documents: [],
  selectedId: null,
  file: null,
  status: null,
  context: null,
  timer: null,
  loading: false,
};
const statuses = {
  queued: "排队中",
  parsing: "解析中",
  publishing: "发布图片",
  ready: "解析完成",
  failed: "处理失败",
};
let apiKey = sessionStorage.getItem("mpr-api-key") || "";
$("#api-key").value = apiKey;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function toast(message, error = false) {
  const target = $("#toast");
  target.textContent = message;
  target.className = `toast${error ? " error" : ""}`;
  target.hidden = false;
  clearTimeout(state.timer);
  state.timer = setTimeout(() => {
    target.hidden = true;
  }, 6000);
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (apiKey) headers.set("X-API-Key", apiKey);
  if (typeof options.body === "string")
    headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, { ...options, headers });
  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = null;
    }
    throw new Error(
      typeof detail === "string" ? detail : `请求失败（${response.status}）`,
    );
  }
  return options.asBlob ? response.blob() : response.json();
}
function view(name) {
  for (const section of document.querySelectorAll(".page-view"))
    section.hidden = section.id !== `view-${name}`;
  for (const button of document.querySelectorAll("[data-view]"))
    button.classList.toggle("active", button.dataset.view === name);
  $("#breadcrumb").textContent = {
    documents: "文档工作台",
    retrieval: "检索实验室",
    settings: "连接与配置",
  }[name];
}
for (const button of document.querySelectorAll("[data-view]"))
  button.addEventListener("click", () => view(button.dataset.view));
function selectFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf"))
    return toast("请选择 PDF 文件。", true);
  if (file.size > (state.status?.max_upload_mb || 25) * 1024 * 1024)
    return toast("文件超过上传大小限制。", true);
  state.file = file;
  $("#selected-file").hidden = false;
  $("#selected-file").textContent =
    `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
  $("#upload-button").disabled = false;
}
$("#file-input").addEventListener("change", (event) =>
  selectFile(event.target.files[0]),
);
for (const eventName of ["dragover", "dragleave", "drop"])
  $("#dropzone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("#dropzone").classList.toggle("dragover", eventName === "dragover");
    if (eventName === "drop") selectFile(event.dataTransfer.files[0]);
  });
$("#upload-button").addEventListener("click", async () => {
  if (!state.file) return;
  const button = $("#upload-button");
  button.disabled = true;
  button.textContent = "正在上传…";
  try {
    const metadata = JSON.parse($("#metadata-input").value);
    if (!metadata || Array.isArray(metadata) || typeof metadata !== "object")
      throw new Error("元数据需要填写 JSON 对象。");
    const form = new FormData();
    form.append("file", state.file);
    form.append("metadata", JSON.stringify(metadata));
    const doc = await api("/documents", { method: "POST", body: form });
    state.selectedId = doc.document_id;
    state.file = null;
    $("#file-input").value = "";
    $("#selected-file").hidden = true;
    toast("已接收文档，正在处理。");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = !state.file;
    button.textContent = "上传并解析 →";
  }
});
function renderDocuments() {
  const docs = state.documents;
  $("#stat-total").textContent = docs.length;
  $("#nav-count").textContent = docs.length;
  $("#list-count").textContent = docs.length;
  $("#stat-ready").textContent = docs.filter(
    (d) => d.status === "ready",
  ).length;
  $("#stat-assets").textContent = docs.reduce(
    (sum, d) => sum + d.assets.length,
    0,
  );
  $("#stat-indexed").textContent = docs.filter(
    (d) => d.index_status === "indexed",
  ).length;
  const list = $("#document-list");
  if (!docs.length) return;
  list.replaceChildren();
  for (const doc of docs) {
    const row = node(
      "button",
      undefined,
      `document-row${state.selectedId === doc.document_id ? " selected" : ""}`,
    );
    row.append(node("span", "PDF", "doc-icon"));
    const info = node("div", undefined, "doc-info");
    info.append(
      node("strong", doc.filename),
      node(
        "small",
        `${new Date(doc.created_at).toLocaleString("zh-CN")} · ${doc.assets.length} 张图片`,
      ),
    );
    row.append(info, node("span", statuses[doc.status], `badge ${doc.status}`));
    row.addEventListener("click", () => {
      state.selectedId = doc.document_id;
      renderDocuments();
      renderDetail();
    });
    list.append(row);
  }
}
function renderDetail() {
  const doc = state.documents.find((d) => d.document_id === state.selectedId);
  $("#document-detail").hidden = !doc;
  if (!doc) return;
  $("#detail-title").textContent = doc.filename;
  $("#markdown-preview").textContent =
    doc.markdown || "文档解析完成后，Markdown 将显示在这里。";
  $("#manifest-preview").textContent = JSON.stringify(doc, null, 2);
  $("#download-button").disabled = doc.status !== "ready";
  $("#retry-button").hidden = doc.status !== "failed";
  $("#index-button").disabled =
    doc.status !== "ready" ||
    !state.status?.ragflow_configured ||
    ["uploading", "indexing", "indexed", "unknown"].includes(doc.index_status);
  $("#index-button").textContent =
    doc.index_status === "indexed"
      ? "索引已完成"
      : doc.index_status === "indexing"
        ? "索引处理中"
        : "提交索引";
  $("#index-refresh-button").hidden = !doc.ragflow_document_id;
  const message = doc.error || doc.index_error;
  $("#detail-message").hidden = !message;
  $("#detail-message").textContent = message || "";
  $("#asset-count").textContent = `(${doc.assets.length})`;
  const gallery = $("#asset-gallery");
  gallery.replaceChildren();
  if (!doc.assets.length)
    gallery.append(
      node(
        "p",
        doc.status === "ready" ? "此文档未提取到图片。" : "等待图片提取。",
        "privacy-note",
      ),
    );
  for (const asset of doc.assets) {
    const link = node("a");
    link.href = asset.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const image = node("img");
    image.src = asset.url;
    image.alt = asset.original_path;
    image.loading = "lazy";
    image.addEventListener("error", () => {
      link.append(node("span", "图片加载失败，请检查公开域名。"));
    });
    link.append(image, node("span", `${asset.original_path} ↗`));
    gallery.append(link);
  }
}
async function refresh(showError = true) {
  if (state.loading) return;
  state.loading = true;
  try {
    const data = await api("/documents");
    state.documents = data.items;
    renderDocuments();
    renderDetail();
  } catch (error) {
    if (showError) toast(error.message, true);
  } finally {
    state.loading = false;
  }
}
$("#refresh-button").addEventListener("click", () => refresh());
$("#retry-button").addEventListener("click", async () => {
  try {
    await api(`/documents/${state.selectedId}/retry`, { method: "POST" });
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});
$("#download-button").addEventListener("click", async () => {
  try {
    const blob = await api(`/documents/${state.selectedId}/markdown`, {
      asBlob: true,
    });
    const link = node("a");
    link.href = URL.createObjectURL(blob);
    link.download = state.documents
      .find((d) => d.document_id === state.selectedId)
      .filename.replace(/\.pdf$/i, ".md");
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) {
    toast(error.message, true);
  }
});
$("#index-button").addEventListener("click", async () => {
  const dataset = $("#dataset-select").value || state.status?.dataset_ids[0];
  if (!dataset) return toast("请先配置 RAGFlow 知识库。", true);
  const button = $("#index-button");
  button.disabled = true;
  try {
    await api(`/documents/${state.selectedId}/index`, {
      method: "POST",
      body: JSON.stringify({ dataset_id: dataset }),
    });
    toast("已提交 RAGFlow，请检查索引状态。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    await refresh();
  }
});
$("#index-refresh-button").addEventListener("click", async () => {
  try {
    await api(`/documents/${state.selectedId}/index/refresh`, {
      method: "POST",
    });
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});
$("#query-button").addEventListener("click", async () => {
  const query = $("#query-input").value.trim();
  if (!query) return toast("请输入检索问题。", true);
  const button = $("#query-button");
  button.disabled = true;
  button.textContent = "正在检索…";
  state.context = null;
  $("#copy-context").disabled = true;
  $("#context-preview").hidden = true;
  $("#response-details").hidden = true;
  $("#evidence-list").replaceChildren();
  $("#query-note").textContent = "正在向知识库检索证据…";
  try {
    const dataset = $("#dataset-select").value;
    const result = await api("/context", {
      method: "POST",
      body: JSON.stringify({
        query,
        dataset_ids: dataset ? [dataset] : [],
        top_k: Number($("#top-k").value),
      }),
    });
    state.context = result;
    $("#context-preview").hidden = !result.context_text;
    $("#context-preview").textContent = result.context_text;
    $("#context-json").textContent = JSON.stringify(result, null, 2);
    $("#response-details").hidden = false;
    $("#copy-context").disabled = !result.context_text;
    $("#query-note").className = "notice";
    $("#query-note").textContent =
      result.missing_information.join(" ") ||
      `找到 ${result.evidence.length} 条证据。片段为检索原文，可用于后续生成。`;
    for (const item of result.evidence) {
      const card = node("article", undefined, "evidence-card");
      card.append(
        node(
          "h3",
          `[${item.citation_id}] ${item.title} · 相关度 ${item.score.toFixed(3)}`,
        ),
        node("p", item.content),
      );
      $("#evidence-list").append(card);
    }
  } catch (error) {
    $("#query-note").className = "notice error";
    $("#query-note").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "检索上下文 →";
  }
});
$("#copy-context").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(state.context.context_text);
    toast("上下文已复制。");
  } catch {
    toast("浏览器未开放剪贴板权限，请选择上下文复制。", true);
  }
});
async function loadStatus() {
  try {
    state.status = await api("/status");
    const status = state.status;
    $("#upload-limit").textContent = `单个文件最大 ${status.max_upload_mb} MB`;
    $("#storage-label").textContent =
      status.storage_backend === "minio"
        ? "MinIO 公开图片链接"
        : "本地 HTTP 图片链接";
    const select = $("#dataset-select");
    select.replaceChildren(new Option("使用配置的知识库", ""));
    for (const id of status.dataset_ids) select.add(new Option(id, id));
    const services = [
      {
        name: "MinerU",
        description: `PDF → Markdown · ${status.mineru_mode} · 目标版本 ${status.mineru_target_version}`,
        configured: status.mineru_configured,
      },
      {
        name: status.storage_backend === "minio" ? "MinIO" : "本地存储",
        description: "图片存储与独立链接访问",
        configured: true,
      },
      {
        name: "RAGFlow",
        description: "索引、混合检索与证据召回",
        configured: status.ragflow_configured,
      },
    ];
    $("#service-list").replaceChildren();
    for (const service of services) {
      const row = node("div", undefined, "service-row");
      const label = node("div", service.name);
      label.append(node("small", service.description));
      row.append(
        label,
        node(
          "span",
          service.configured ? "已配置 · 待调用验证" : "待配置",
          "badge",
        ),
      );
      $("#service-list").append(row);
    }
  } catch (error) {
    toast(error.message, true);
    view("settings");
  }
}
$("#save-key").addEventListener("click", async () => {
  apiKey = $("#api-key").value.trim();
  sessionStorage.setItem("mpr-api-key", apiKey);
  await loadStatus();
  await refresh();
});
async function start() {
  await loadStatus();
  await refresh(false);
}
start();
setInterval(() => {
  if (
    !document.hidden &&
    state.documents.some((d) =>
      ["queued", "parsing", "publishing"].includes(d.status),
    )
  )
    refresh(false);
}, 2500);
