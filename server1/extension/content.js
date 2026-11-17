(() => {
  if (document.getElementById("captcha-server1-bridge")) return;
  const bridge = document.createElement("div");
  bridge.id = "captcha-server1-bridge";
  bridge.hidden = true;
  document.documentElement.appendChild(bridge);
  const panel = document.createElement("div");
  panel.id = "captcha-server1-panel";
  const shadow = panel.attachShadow({mode: "open"});
  const label = document.createElement("div");
  label.style.cssText = "position:fixed;bottom:12px;right:12px;z-index:2147483647;background:#263238;color:white;padding:10px 14px;border-radius:7px;font:13px/1.5 sans-serif;max-width:340px;box-shadow:0 2px 12px #0005";
  shadow.appendChild(label);
  if (window === window.top) document.documentElement.appendChild(panel);
  const mode = CAPTCHA_SERVER1_CONFIG.mode || "diagnostics";
  let state = {status: "connecting", serverReady: false, mode, serverUrl: CAPTCHA_SERVER1_CONFIG.serverUrl};
  let busy = false;
  let lastSignature = "";
  let requestId = 0;
  function publish(values) {
    state = {...state, ...values};
    bridge.setAttribute("data-state", JSON.stringify(state));
    const messages = {
      connecting: "Extension: đã nạp · Đang nối server…",
      ready: mode === "fixture" ? "Trang mẫu local: sẵn sàng kiểm tra ảnh" : "Chẩn đoán kết nối: đã nối · Không chạy nhận diện ảnh",
      detecting: "Server đang xử lý ảnh CAPTCHA…",
      detected: `Server đã trả ${state.result?.points?.length || 0} điểm (${state.result?.source || "detect"})`,
      error: `Kết nối/test lỗi: ${state.error || "không rõ"}`
    };
    label.textContent = messages[state.status] + "\n" + state.serverUrl;
    label.style.whiteSpace = "pre-line";
    label.style.background = state.status === "error" ? "#a32020" : state.serverReady ? "#185e39" : "#263238";
  }
  publish({});
  async function health() {
    try {
      const response = await chrome.runtime.sendMessage({action: "server1_health"});
      if (!response?.ok) throw Object.assign(new Error(response?.error || "Không nhận được phản hồi server"), {code: response?.code});
      publish({serverReady: true, health: response.data, lastHealthAt: new Date().toISOString(), ...(state.status === "connecting" || !state.serverReady ? {status: "ready", error: null, errorCode: null} : {})});
    } catch (error) {
      publish({status: "error", serverReady: false, errorCode: error.code || "connection_failed", error: error.message});
    }
  }
  function visible(element) {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width >= 180 && rect.height >= 70 && style.display !== "none" && style.visibility !== "hidden";
  }
  function background() {
    const roots = ".shopee-captcha, .shopee-captcha-slider__main, .er1GfA, [class*='captcha' i]";
    const selector = CAPTCHA_SERVER1_CONFIG.backgroundSelector ||
      roots.split(", ").map(root => `${root} img, ${root} canvas`).join(", ");
    const images = [...document.querySelectorAll(selector)].filter(visible)
      .filter(image => image.tagName === "CANVAS" || image.complete && image.naturalWidth);
    images.sort((a, b) => b.getBoundingClientRect().width * b.getBoundingClientRect().height - a.getBoundingClientRect().width * a.getBoundingClientRect().height);
    return images[0];
  }
  async function dataUrl(image) {
    if (image.tagName === "CANVAS") return image.toDataURL("image/png");
    const source = image.currentSrc || image.src;
    if (source.startsWith("data:image/")) return source;
    const response = await fetch(source, {credentials: "include"});
    if (!response.ok) throw new Error(`Không tải được ảnh gốc: HTTP ${response.status}`);
    const blob = await response.blob();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Không đọc được ảnh gốc"));
      reader.readAsDataURL(blob);
    });
  }
  async function inspect() {
    if (!CAPTCHA_SERVER1_CONFIG.autoDetect) return;
    if (busy || !state.serverReady) return;
    const image = background();
    if (!image) return;
    const signature = image.tagName === "CANVAS" ? image.toDataURL("image/png") : image.currentSrc || image.src;
    if (!signature || signature === lastSignature) return;
    lastSignature = signature;
    busy = true;
    const id = ++requestId;
    try {
      document.querySelectorAll("[data-captcha-server1-image]").forEach(node => node.removeAttribute("data-captcha-server1-image"));
      image.setAttribute("data-captcha-server1-image", String(id));
      const dimensions = {width: image.naturalWidth || image.width, height: image.naturalHeight || image.height};
      publish({status: "detecting", requestId: id, dimensions, error: null, result: null});
      const encoded = await dataUrl(image);
      const response = await chrome.runtime.sendMessage({action: "server1_detect", image: encoded});
      if (!response?.ok) throw new Error(response?.error || "Không có kết quả /detect");
      publish({status: "detected", requestId: id, result: response.data});
    } catch (error) {
      publish({status: "error", error: error.message});
    } finally {
      busy = false;
    }
  }
  health();
  setInterval(health, 10000);
  setInterval(() => inspect().catch(error => publish({status: "error", error: error.message})), 500);
})();
