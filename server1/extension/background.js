importScripts("config.js");

async function serverRequest(path, body) {
  const options = {signal: AbortSignal.timeout(body ? 65000 : 10000)};
  if (body) {
    options.method = "POST";
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(CAPTCHA_SERVER1_CONFIG.serverUrl + path, options);
  } catch (error) {
    throw Object.assign(new Error(error.name === "TimeoutError" ? "Server không phản hồi trong thời gian chờ" : "Không kết nối được server local"), {code: error.name === "TimeoutError" ? "server_timeout" : "server_unreachable"});
  }
  let data;
  try {
    data = await response.json();
  } catch (error) {
    throw Object.assign(new Error(`Server trả HTTP ${response.status}, không phải JSON`), {code: "invalid_json"});
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) throw Object.assign(new Error("JSON phản hồi không phải một object"), {code: "invalid_json"});
  if (!response.ok || !data.ok) throw Object.assign(new Error(data.error || `HTTP ${response.status}`), {code: data.code || "http_error"});
  if (path === "/health" && data.service !== "captcha-so-pe-server1") throw Object.assign(new Error("Địa chỉ này không phải server1"), {code: "wrong_service"});
  return data;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!message || !["server1_health", "server1_detect"].includes(message.action)) return false;
  const operation = message.action === "server1_health"
    ? serverRequest("/health") : serverRequest("/detect", {image: message.image});
  operation.then(data => respond({ok: true, data, serverUrl: CAPTCHA_SERVER1_CONFIG.serverUrl}))
    .catch(error => respond({ok: false, code: error.code || "request_failed", error: error.message, serverUrl: CAPTCHA_SERVER1_CONFIG.serverUrl}));
  return true;
});
