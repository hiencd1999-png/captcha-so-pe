const state = document.getElementById("server-state");
const link = document.getElementById("server-url");
link.textContent = CAPTCHA_SERVER1_CONFIG.serverUrl;
link.href = CAPTCHA_SERVER1_CONFIG.serverUrl + "/health";
async function refresh() {
  try {
    const response = await chrome.runtime.sendMessage({action: "server1_health"});
    if (!response?.ok) throw new Error(response?.error || "Không nhận được phản hồi");
    state.dataset.ready = "true";
    state.style.background = "#e0f3e6";
    state.textContent = `Server: đang chạy\nModel YOLO: ${response.data.model_loaded ? "đã nạp" : "chưa nạp"}\nAPI: ${response.serverUrl}/detect`;
  } catch (error) {
    state.dataset.ready = "false";
    state.style.background = "#ffe4e4";
    state.textContent = "Server chưa kết nối: " + error.message;
  }
}
refresh();
setInterval(refresh, 5000);
