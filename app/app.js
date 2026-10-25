"use strict";

const $ = (id) => document.getElementById(id);
const state = { catalog: null, busy: false };
const shortNames = {tfidf_logistic: "TF-IDF + LR", tfidf_rf: "TF-IDF + RF", fasttext: "fastText", bert: "BERT", llm: "LLM"};
const symbols = {tfidf_logistic: "LR", tfidf_rf: "RF", fasttext: "FT", bert: "B", llm: "LLM"};
const samples = ["这个活动很好玩", "这个版本太让人失望了，根本不好玩", "今天更新了新版本"];
const classes = {0: "negative", 1: "positive", 2: "neutral"};

function comments() { return $("comments").value.split(/\r?\n/).map((text) => text.trim()).filter(Boolean); }
function selectedModel() { return state.catalog?.models.find((model) => model.id === $("version-select").value); }
function message(text = "", error = false) { $("message").textContent = text; $("message").classList.toggle("error", error); }
function updateInput() {
  const count = comments().length;
  $("text-count").textContent = `${count} 条`;
  $("predict-btn").disabled = state.busy || !selectedModel() || !count;
}
function resetResults() {
  $("empty-results").hidden = false;
  $("results-content").hidden = true;
  $("result-list").replaceChildren();
  $("result-timing").textContent = "等待分析";
}
function formatVersion(version) {
  const match = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/.exec(version);
  return match ? `${match[1]}.${match[2]}.${match[3]}  ${match[4]}:${match[5]}:${match[6]} UTC` : version;
}
function modelVersion(model) {
  const variants = {original: "原始", quantized: "量化", distilled: "蒸馏"};
  return `${formatVersion(model.version)} · ${variants[model.variant] || "原始"}`;
}
function updateModelInfo() {
  const model = selectedModel();
  $("model-score").textContent = model?.dev_macro_f1 != null ? `${(model.dev_macro_f1 * 100).toFixed(2)}%` : "—";
  $("training-count").textContent = model?.train_rows != null ? model.train_rows.toLocaleString("zh-CN") : "—";
  $("model-note").textContent = model ? "模型已就绪 · 加载已训练模型进行预测" : "暂无可用模型，请先完成训练并刷新模型列表";
  for (const button of $("algorithm-list").children) button.classList.toggle("active", button.dataset.algorithm === $("algorithm-select").value);
  resetResults(); message(); updateInput();
}
function updateVersions(preferredId) {
  const models = state.catalog.models.filter((model) => model.algorithm === $("algorithm-select").value);
  $("version-select").replaceChildren();
  for (const model of models) $("version-select").append(new Option(modelVersion(model), model.id));
  if (!models.length) $("version-select").append(new Option("暂无已保存模型", ""));
  if (models.some((model) => model.id === preferredId)) $("version-select").value = preferredId;
  $("version-select").disabled = state.busy || !models.length;
  updateModelInfo();
}
async function getJSON(url, options) {
  const response = await fetch(url, options);
  let data;
  try { data = await response.json(); } catch { throw new Error("后端返回异常，请检查服务是否正常运行"); }
  if (!response.ok) throw new Error(data.error || "请求失败，请稍后再试");
  return data;
}
async function refreshModels() {
  const previousId = $("version-select").value;
  $("refresh-btn").disabled = true;
  try {
    const data = await getJSON("/api/models");
    state.catalog = data;
    $("algorithm-select").replaceChildren(); $("algorithm-list").replaceChildren();
    for (const algorithm of data.algorithms) {
      const option = new Option(`${algorithm.name}${algorithm.available ? "" : ` · ${algorithm.status}`}`, algorithm.id);
      option.disabled = !algorithm.available;
      $("algorithm-select").append(option);
      const button = document.createElement("button");
      button.type = "button"; button.className = "algorithm-item";
      button.disabled = !algorithm.available; button.dataset.algorithm = algorithm.id;
      const symbol = document.createElement("span"); symbol.className = "algo-symbol"; symbol.textContent = symbols[algorithm.id] || "M";
      const name = document.createElement("span"); name.className = "algorithm-name"; name.textContent = shortNames[algorithm.id] || algorithm.name;
      const status = document.createElement("small"); status.textContent = algorithm.available ? "就绪" : algorithm.status === "尚无已保存模型" ? "待训练" : "待接入";
      button.append(symbol, name, status);
      button.addEventListener("click", () => { if (!state.busy) { $("algorithm-select").value = algorithm.id; updateVersions(); } });
      $("algorithm-list").append(button);
    }
    const chosen = data.models.find((model) => model.id === previousId) || data.models.find((model) => model.id === data.default_model_id);
    if (chosen) $("algorithm-select").value = chosen.algorithm;
    $("algorithm-select").disabled = !data.models.length;
    updateVersions(chosen?.id);
    $("service-status").className = "service-status connected";
    $("service-status").replaceChildren(Object.assign(document.createElement("i"), {}), document.createTextNode("本地服务已连接"));
    $("algorithm-count").textContent = `${data.algorithms.length}`;
    $("available-count").textContent = `${data.models.length} 个模型版本可用`;
  } catch (error) {
    state.catalog = null;
    $("service-status").className = "service-status error";
    $("service-status").textContent = "连接失败";
    $("available-count").textContent = "请检查本地后端";
    $("algorithm-select").disabled = true; $("version-select").disabled = true;
    $("predict-btn").disabled = true;
    message(error.message, true);
  } finally { $("refresh-btn").disabled = false; }
}
function setBusy(busy) {
  state.busy = busy;
  $("predict-btn").classList.toggle("loading", busy);
  $("predict-label").textContent = busy ? "正在分析…" : "开始预测";
  for (const id of ["comments", "sample-btn", "clear-btn", "refresh-btn"]) $(id).disabled = busy;
  $("algorithm-select").disabled = busy || !state.catalog?.models.length;
  $("version-select").disabled = busy || !selectedModel();
  updateInput();
}
function renderResults(data) {
  $("summary").replaceChildren(); $("result-list").replaceChildren();
  for (const [key, label, type] of [["positive", "正面", "positive"], ["negative", "负面", "negative"], ["neutral", "中性", "neutral"]]) {
    const chip = document.createElement("div"); chip.className = `summary-chip ${type}`;
    const name = document.createElement("span"); name.textContent = label;
    const count = document.createElement("strong"); count.textContent = `${data.counts[key]}`;
    chip.append(name, count); $("summary").append(chip);
  }
  data.predictions.forEach((item, index) => {
    const li = document.createElement("li"); li.className = "result-item";
    const header = document.createElement("div"); header.className = "result-item-header";
    const number = document.createElement("span"); number.className = "result-number"; number.textContent = `COMMENT ${String(index + 1).padStart(2, "0")}`;
    const badge = document.createElement("span"); badge.className = `sentiment-badge ${classes[item.label]}`; badge.textContent = item.sentiment;
    const text = document.createElement("p"); text.className = "result-comment"; text.textContent = item.comment;
    header.append(number, badge); li.append(header, text); $("result-list").append(li);
  });
  $("result-model").textContent = `${data.model.name} · ${modelVersion(data.model)}`;
  $("result-timing").textContent = `${data.predictions.length} 条 · ${(data.elapsed_ms / 1000).toFixed(2)} 秒`;
  $("empty-results").hidden = true; $("results-content").hidden = false;
}
async function runPrediction() {
  if (state.busy) return;
  const texts = comments(), model = selectedModel();
  if (!model) return message("请先选择一个已就绪的模型", true);
  if (!texts.length) return message("请先输入至少一条评论", true);
  if (texts.length > 100) return message("每次最多预测 100 条评论", true);
  if (texts.some((text) => text.length > 5000)) return message("单条评论不能超过 5000 字", true);
  setBusy(true); message("正在读取模型并分析评论…");
  try {
    const data = await getJSON("/api/predict", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({model_id: model.id, texts})});
    renderResults(data); message(`已完成 ${data.predictions.length} 条评论的预测`);
  } catch (error) { message(error.message, true); }
  finally { setBusy(false); }
}
$("comments").addEventListener("input", () => { updateInput(); message(); });
$("comments").addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); runPrediction(); } });
$("sample-btn").addEventListener("click", () => { $("comments").value = samples.join("\n"); updateInput(); resetResults(); message(); $("comments").focus(); });
$("clear-btn").addEventListener("click", () => { $("comments").value = ""; updateInput(); resetResults(); message(); $("comments").focus(); });
$("algorithm-select").addEventListener("change", () => updateVersions());
$("version-select").addEventListener("change", updateModelInfo);
$("refresh-btn").addEventListener("click", refreshModels);
$("predict-btn").addEventListener("click", runPrediction);
refreshModels();
