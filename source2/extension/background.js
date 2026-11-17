chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "solve_captcha") {
        fetch(request.url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: request.image })
        })
        .then(response => response.json())
        .then(data => sendResponse({ success: true, data }))
        .catch(error => sendResponse({ success: false, error: error.message }));
        return true; // Keep channel open for async response
    }
});
