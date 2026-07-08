const state = {
  people: [],
  faceSamples: [],
  lastIotCommandId: null,
  telemetryIndex: 0,
  yoloTarget: "light_bulb",
};

const DEVICE_ID = "orange-pi-main";
const LABEL_TEXT = {
  drone: "无人机",
  fire_extinguisher: "灭火器",
  light_bulb: "灯泡",
  person: "人",
  car: "车辆",
  unknown: "未知",
};
const REAL_YOLO_SAMPLES = {
  drone: {
    url: "/real-samples/YOLO识别/无人机/无人机_drone_01.jpg",
    filename: "drone_real_01.jpg",
  },
  fire_extinguisher: {
    url: "/real-samples/YOLO识别/灭火器/灭火器_fire_extinguisher_01.jpg",
    filename: "fire_extinguisher_real_01.jpg",
  },
  light_bulb: {
    url: "/real-samples/YOLO识别/灯泡/灯泡_light_bulb_01.jpeg",
    filename: "light_bulb_real_01.jpeg",
  },
};

const $ = (id) => document.getElementById(id);

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

function unwrap(payload) {
  return payload && Object.prototype.hasOwnProperty.call(payload, "data") ? payload.data : payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBool(value, openText, closedText) {
  return value ? openText : closedText;
}

function formatTime(value) {
  if (!value) return "--";
  return String(value).replace("T", " ").replace("+08:00", "");
}

function labelName(label) {
  return LABEL_TEXT[label] || label || "未知";
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function setVerdict(id, type, text) {
  const el = $(id);
  el.className = `verdict ${type}`;
  el.textContent = text;
}

function updateMetrics(sensor) {
  $("temperature").textContent = `${Number(sensor.temperature || 0).toFixed(1)}°C`;
  $("fan-state").textContent = `风扇 ${sensor.fan_on ? "开启" : "关闭"}`;
  $("door-state").textContent = formatBool(sensor.door_open, "开启", "关闭");
  $("window-state").textContent = formatBool(sensor.window_open, "开启", "关闭");
  $("light-level").textContent = `${sensor.light_level ?? 0}%`;
  $("brightness").value = sensor.light_level ?? 80;
  $("brightness-value").textContent = `${sensor.light_level ?? 80}%`;
}

function updatePeople(people) {
  state.people = people;
}

function updateCommands(commands) {
  const list = $("pending-commands");
  list.innerHTML = "";
  if (!commands.length) {
    const item = document.createElement("li");
    item.textContent = "暂无待执行命令";
    list.appendChild(item);
    return;
  }
  for (const command of commands) {
    const item = document.createElement("li");
    item.textContent = `#${command.id} ${command.device} · ${command.action}${command.value ? ` · ${command.value}` : ""}`;
    list.appendChild(item);
  }
}

async function refreshState() {
  const data = await api("/api/state");
  $("health-pill").textContent = "后端已连接";
  updateMetrics(data.sensor);
  updatePeople(data.people);
  updateCommands(data.pending_commands);
}

function setYoloTarget(target) {
  state.yoloTarget = target;
  $("yolo-target").value = target;
  document.querySelectorAll("[data-yolo-target]").forEach((button) => {
    button.classList.toggle("active", button.dataset.yoloTarget === target);
  });
  setVerdict("yolo-verdict", "neutral", `准备测试：${labelName(target)}`);
}

function yoloResultText(result, expectedLabel) {
  const detection = result.detection || {};
  const labels = detection.labels || [];
  const objects = detection.objects || [];
  const rows = labels.length ? labels : objects;
  const commands = result.auto_commands || [];
  const found = rows.some((item) => item.label === expectedLabel);
  const top = rows[0] || { label: "unknown", confidence: 0 };
  const commandText = commands.length
    ? commands.map((cmd) => `${cmd.device}:${cmd.action}${cmd.value ? `=${cmd.value}` : ""}`).join(", ")
    : "无";

  setVerdict(
    "yolo-verdict",
    found ? "pass" : "warn",
    found ? `识别通过：${labelName(expectedLabel)}` : `未命中期望：当前 ${labelName(top.label)}`,
  );

  return [
    `测试类别: ${labelName(expectedLabel)} (${expectedLabel})`,
    `最高结果: ${labelName(top.label)} (${top.label})`,
    `最高置信度: ${percent(top.confidence)}`,
    `识别引擎: ${detection.engine || "--"}`,
    "",
    "全部标签:",
    rows.length
      ? rows.map((item) => `- ${labelName(item.label)} (${item.label}) · ${percent(item.confidence)}`).join("\n")
      : "- 未检测到目标",
    "",
    `灯泡联动命令: ${commandText}`,
    expectedLabel === "light_bulb" ? "期望效果: 识别灯泡后生成 SET_LIGHT level=80" : "期望效果: 展示目标识别结果，不触发门禁开门",
  ].join("\n");
}

async function uploadYoloImage(event) {
  event.preventDefault();
  const input = $("yolo-image-input");
  if (!input.files.length) return;

  const expectedLabel = $("yolo-target").value;
  const form = new FormData();
  form.append("image", input.files[0]);
  form.append("image_type", DEVICE_ID);

  const result = await api("/api/images", { method: "POST", body: form });
  $("yolo-preview").src = result.image_url;
  $("yolo-result").textContent = yoloResultText(result, expectedLabel);
  toast("YOLO 识别完成");
  await refreshAll();
}

async function useRealYoloSample(target) {
  const sample = REAL_YOLO_SAMPLES[target];
  if (!sample) throw new Error("未配置该类别真实样本");
  setYoloTarget(target);
  const response = await fetch(encodeURI(sample.url));
  if (!response.ok) throw new Error("真实测试样本不存在，请先运行 prepare_real_demo_dataset.py");
  const blob = await response.blob();
  const file = new File([blob], sample.filename, { type: blob.type || "image/jpeg" });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  $("yolo-image-input").files = transfer.files;
  $("yolo-preview").src = URL.createObjectURL(file);
  toast(`已载入真实${labelName(target)}样本，点击运行 YOLO 识别`);
}

function updateFaceSamples(payload) {
  const data = unwrap(payload);
  const people = data.people || [];
  const samples = data.samples || [];
  state.faceSamples = samples;

  const list = $("registered-faces");
  list.innerHTML = "";
  if (!people.length) {
    const item = document.createElement("li");
    item.textContent = "暂无真实人脸样本，请先运行 prepare_lfw_face_samples.py";
    list.appendChild(item);
  } else {
    for (const person of people) {
      const item = document.createElement("li");
      item.textContent = `${person.person_id} · ${person.name} · ${person.sample_count} 张注册照`;
      list.appendChild(item);
    }
  }

  const select = $("face-sample");
  const current = select.value;
  select.innerHTML = "";
  if (!samples.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无测试样本";
    select.appendChild(option);
  } else {
    for (const sample of samples) {
      const option = document.createElement("option");
      option.value = sample.sample_id;
      option.textContent = `${sample.sample_id} · ${sample.name} · 期望 ${sample.expected_decision}`;
      select.appendChild(option);
    }
  }
  if (current) select.value = current;
}

async function loadFaceSamples() {
  updateFaceSamples(await api("/api/face/samples"));
}

function faceResultText(result) {
  const data = unwrap(result);
  const sample = data.sample;
  const passed = sample ? data.passed : data.decision === "allow" || data.decision === "deny";
  setVerdict(
    "face-verdict",
    passed ? "pass" : "warn",
    `${data.decision === "allow" ? "允许 allow" : "拒绝 deny"}${sample ? ` · ${data.passed ? "符合期望" : "不符合期望"}` : ""}`,
  );
  return [
    sample ? `样本: ${sample.sample_id} · ${sample.name}` : "样本: 上传图片",
    `判定: ${data.decision === "allow" ? "允许 allow" : "拒绝 deny"}`,
    `匹配: ${data.matched_name || "未匹配"}`,
    `置信度: ${percent(data.confidence)}`,
    `相似度: ${data.similarity ?? "--"}`,
    `阈值: ${data.threshold ?? "--"}`,
    `引擎: ${data.engine || "--"}`,
    sample ? `期望: ${data.expected_decision} · ${data.passed ? "通过" : "失败"}` : "",
    `说明: ${data.message || ""}`,
  ]
    .filter(Boolean)
    .join("\n");
}

async function verifyFaceSample(event) {
  event.preventDefault();
  await verifyFaceSampleById($("face-sample").value);
}

async function verifyFaceSampleById(sampleId) {
  if (!sampleId) return;
  const sample = state.faceSamples.find((item) => item.sample_id === sampleId);
  if (!sample) {
    toast("人脸样本尚未加载，请先刷新样本");
    return;
  }
  $("face-sample").value = sampleId;
  const form = new FormData();
  form.append("sample_id", sampleId);
  const result = await api("/api/face/verify", { method: "POST", body: form });
  $("face-preview").src = sample.file_url;
  $("face-result").textContent = faceResultText(result);
  toast("人脸样本验证完成");
}

async function uploadFaceForVerify(event) {
  event.preventDefault();
  const input = $("face-upload-input");
  if (!input.files.length) return;
  const form = new FormData();
  form.append("image", input.files[0]);
  const result = await api("/api/face/verify", { method: "POST", body: form });
  const data = unwrap(result);
  if (data.file_url) $("face-preview").src = data.file_url;
  $("face-result").textContent = faceResultText(result);
  toast("上传人脸验证完成");
}

async function sendControl(device, action, value = null) {
  await api("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device, action, value, source: "gui" }),
  });
  toast(`已发送命令：${device} ${action}`);
  await refreshAll();
}

async function simulateSensor() {
  await api("/api/demo/sensors", { method: "POST" });
  toast("已生成一条模拟传感器数据");
  await refreshAll();
}

function renderDevices(devices) {
  const list = $("iot-devices");
  list.innerHTML = "";
  if (!devices.length) {
    const item = document.createElement("li");
    item.textContent = "暂无设备";
    list.appendChild(item);
    return;
  }
  for (const device of devices) {
    const item = document.createElement("li");
    item.textContent = `${device.id} · ${device.type} · ${device.online ? "在线" : "离线"} · ${formatTime(device.last_seen)}`;
    list.appendChild(item);
  }
}

function renderLatestStatus(status) {
  $("iot-latest").textContent = [
    `设备: ${status.device_id || "--"}`,
    `采集时间: ${formatTime(status.captured_at)}`,
    `温度: ${Number(status.temperature_c || 0).toFixed(1)}°C`,
    `门: ${status.door_open ? "开启" : "关闭"}`,
    `窗: ${status.window_open ? "开启" : "关闭"}`,
    `灯光: ${status.light_level ?? 0}%`,
    `风扇: ${status.fan_on ? "开启" : "关闭"}`,
  ].join("\n");
}

function renderEvents(events) {
  const list = $("iot-events");
  list.innerHTML = "";
  if (!events.length) {
    const item = document.createElement("li");
    item.textContent = "暂无事件";
    list.appendChild(item);
    return;
  }
  for (const event of events) {
    const item = document.createElement("li");
    item.innerHTML = `<strong>${escapeHtml(event.title || event.type)}</strong><span>${escapeHtml(event.message || "")}</span><small>${escapeHtml(formatTime(event.created_at))}</small>`;
    list.appendChild(item);
  }
}

async function loadIotStatus() {
  const [devicesPayload, latestPayload, eventsPayload] = await Promise.all([
    api("/api/devices"),
    api("/api/status/latest"),
    api("/api/events?limit=8"),
  ]);
  renderDevices(unwrap(devicesPayload) || []);
  renderLatestStatus(unwrap(latestPayload) || {});
  renderEvents(unwrap(eventsPayload) || []);
}

async function runTelemetryDemo() {
  state.telemetryIndex += 1;
  const light = 45 + ((state.telemetryIndex * 13) % 45);
  const temperature = 27 + (state.telemetryIndex % 5);
  await api("/api/device/telemetry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      device_id: DEVICE_ID,
      captured_at: new Date().toISOString(),
      temperature_c: temperature,
      door_open: state.telemetryIndex % 2 === 0,
      window_open: state.telemetryIndex % 2 === 1,
      light_level: light,
      fan_on: temperature >= 30,
    }),
  });
  toast("设备状态已上报");
  await refreshAll();
}

async function sendIotLightCommand() {
  const level = Number($("brightness").value || 80);
  const payload = await api("/api/commands", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: DEVICE_ID, type: "SET_LIGHT", payload: { level } }),
  });
  const command = unwrap(payload);
  state.lastIotCommandId = command.id;
  toast(`已下发 SET_LIGHT 命令 #${command.id}`);
  await refreshAll();
}

async function ackLatestCommand() {
  const payload = await api(`/api/device/commands/pending?device_id=${encodeURIComponent(DEVICE_ID)}`);
  const pending = unwrap(payload) || [];
  const command = pending.find((item) => item.id === state.lastIotCommandId) || pending[0];
  if (!command) {
    toast("暂无待确认命令");
    return;
  }
  await api(`/api/device/commands/${command.id}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: DEVICE_ID, status: "done", message: "GUI demo ack" }),
  });
  toast(`设备已确认命令 #${command.id}`);
  await refreshAll();
}

function tableHeaders(kind) {
  if (kind === "commands") return ["ID", "设备", "动作", "值", "来源", "状态", "时间"];
  if (kind === "images") return ["ID", "图片", "目标", "人脸", "门禁", "时间"];
  if (kind === "events") return ["ID", "类型", "标题", "内容", "时间"];
  return ["ID", "温度", "门", "窗", "灯光", "风扇", "来源", "时间"];
}

function tableRow(kind, item) {
  if (kind === "commands") {
    return [item.id, item.device, item.action, item.value || "", item.source, item.status, formatTime(item.created_at)];
  }
  if (kind === "images") {
    const object = item.detection?.objects?.[0]?.label || item.detection?.labels?.[0]?.label || "unknown";
    return [
      item.id,
      item.original_name,
      `${labelName(object)} (${object})`,
      item.face?.name || item.face?.matched_name || "Unknown",
      item.face?.access_allowed ? "允许" : "关闭",
      formatTime(item.captured_at || item.created_at),
    ];
  }
  if (kind === "events") {
    return [item.id, item.type, item.title, item.message, formatTime(item.created_at)];
  }
  return [
    item.id,
    `${Number(item.temperature || 0).toFixed(1)}°C`,
    item.door_open ? "开" : "关",
    item.window_open ? "开" : "关",
    `${item.light_level}%`,
    item.fan_on ? "开" : "关",
    item.source,
    formatTime(item.captured_at || item.created_at),
  ];
}

async function loadHistory() {
  const kind = $("history-kind").value;
  const payload =
    kind === "events"
      ? await api("/api/events?limit=30")
      : await api(`/api/history?kind=${encodeURIComponent(kind)}&limit=30`);
  const items = kind === "events" ? unwrap(payload) : payload.items;
  $("history-head").innerHTML = `<tr>${tableHeaders(kind).map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr>`;
  $("history-body").innerHTML = (items || [])
    .map((item) => `<tr>${tableRow(kind, item).map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`)
    .join("");
}

async function refreshAll() {
  await refreshState();
  await loadIotStatus();
  await loadHistory();
}

function bindEvents() {
  $("yolo-form").addEventListener("submit", (event) => uploadYoloImage(event).catch((error) => toast(error.message)));
  $("yolo-target").addEventListener("change", (event) => setYoloTarget(event.target.value));
  document.querySelectorAll("[data-real-yolo-sample]").forEach((button) => {
    button.addEventListener("click", () => useRealYoloSample(button.dataset.realYoloSample).catch((error) => toast(error.message)));
  });
  document.querySelectorAll("[data-yolo-target]").forEach((button) => {
    button.addEventListener("click", () => setYoloTarget(button.dataset.yoloTarget));
  });

  $("simulate-sensor").addEventListener("click", () => simulateSensor().catch((error) => toast(error.message)));
  $("history-kind").addEventListener("change", () => loadHistory().catch((error) => toast(error.message)));
  $("refresh-face-samples").addEventListener("click", () => loadFaceSamples().catch((error) => toast(error.message)));
  $("face-sample-form").addEventListener("submit", (event) => verifyFaceSample(event).catch((error) => toast(error.message)));
  document.querySelectorAll("[data-face-sample-id]").forEach((button) => {
    button.addEventListener("click", () => verifyFaceSampleById(button.dataset.faceSampleId).catch((error) => toast(error.message)));
  });
  $("face-upload-form").addEventListener("submit", (event) => uploadFaceForVerify(event).catch((error) => toast(error.message)));
  $("refresh-iot").addEventListener("click", () => loadIotStatus().catch((error) => toast(error.message)));
  $("iot-telemetry-demo").addEventListener("click", () => runTelemetryDemo().catch((error) => toast(error.message)));
  $("iot-command-demo").addEventListener("click", () => sendIotLightCommand().catch((error) => toast(error.message)));
  $("iot-ack-demo").addEventListener("click", () => ackLatestCommand().catch((error) => toast(error.message)));
  $("brightness").addEventListener("input", (event) => {
    $("brightness-value").textContent = `${event.target.value}%`;
  });
  $("send-brightness").addEventListener("click", () => {
    sendControl("light", "set_brightness", $("brightness").value).catch((error) => toast(error.message));
  });
  document.querySelectorAll(".control-grid button").forEach((button) => {
    button.addEventListener("click", () => {
      sendControl(button.dataset.device, button.dataset.action, button.dataset.value ?? null).catch((error) =>
        toast(error.message),
      );
    });
  });
}

async function boot() {
  bindEvents();
  setYoloTarget("light_bulb");
  await refreshState();
  await loadFaceSamples();
  await loadIotStatus();
  await loadHistory();
}

boot().catch((error) => {
  $("health-pill").textContent = "后端未连接";
  toast(error.message);
});
