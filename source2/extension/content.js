(function () {
    console.log("Shopee Captcha Solver loaded - Multi-solve & 2D drag");

    let solving = false; // đang giải hay không

    async function solveCaptcha() {
        if (solving) {
            console.log("Đã có captcha đang được giải, bỏ qua.");
            return;
        }
        solving = true;
        console.log("Bắt đầu giải captcha...");

        const container = document.querySelector('.er1GfA') || document.querySelector('.shopee-captcha-slider__main') || document.body;
        const allImgs = Array.from(container.querySelectorAll('img')).filter(img => {
            const visible = img.offsetParent !== null && getComputedStyle(img).display !== 'none';
            return visible && img.src && img.src.startsWith('data:image');
        });

        console.log(`Tìm thấy ${allImgs.length} ảnh data-uri.`);

        // Tìm mảnh ghép có style left và top (position absolute)
        let pieceImg = null;
        for (let img of allImgs) {
            const left = parseFloat(img.style.left);
            const top = parseFloat(img.style.top);
            if (!isNaN(left) && !isNaN(top) && img.style.position === 'absolute') {
                pieceImg = img;
                break;
            }
        }
        if (!pieceImg) {
            pieceImg = allImgs.find(img => !isNaN(parseFloat(img.style.left)) && !isNaN(parseFloat(img.style.top)));
        }

        if (!pieceImg) {
            console.warn("Không tìm thấy mảnh ghép (cần có left và top).");
            solving = false;
            return;
        }

        const currentLeft = parseFloat(pieceImg.style.left) || 0;
        const currentTop = parseFloat(pieceImg.style.top) || 0;
        console.log(`Vị trí hiện tại: left=${currentLeft}px, top=${currentTop}px`);

        try {
            const url = 'http://103.67.196.23:5000/solve';
            chrome.runtime.sendMessage({
                action: "solve_captcha",
                url: url,
                image: pieceImg.src
            }, (response) => {
                if (response && response.success) {
                    const result = response.data;
                    console.log("Kết quả từ server:", result);

                    let targetLeft = result.left ?? null;
                    let targetTop = result.top ?? null;

                    // Nếu server trả về match dạng "left_top.png"
                    if (result.match && typeof result.match === 'string') {
                        const parts = result.match.split('_');
                        if (parts.length >= 2) {
                            if (targetLeft === null) targetLeft = parseInt(parts[0], 10);
                            if (targetTop === null) targetTop = parseInt(parts[1], 10);
                        }
                    }

                    if (targetLeft !== null && targetTop !== null) {
                        const distanceX = targetLeft - currentLeft;
                        const distanceY = targetTop - currentTop;
                        console.log(`Đích đến: left=${targetLeft}px, top=${targetTop}px`);
                        console.log(`Cần kéo: ${distanceX > 0 ? 'phải' : 'trái'} ${Math.abs(distanceX)}px, ${distanceY > 0 ? 'xuống' : 'lên'} ${Math.abs(distanceY)}px`);

                        if (Math.abs(distanceX) < 300 && Math.abs(distanceY) < 200) {
                            dragPiece2D(pieceImg, distanceX, distanceY, currentLeft, currentTop);
                        } else {
                            console.error("Khoảng cách không hợp lệ:", distanceX, distanceY);
                            solving = false;
                        }
                    } else {
                        console.error("Server không trả left/top hoặc match.");
                        solving = false;
                    }
                } else {
                    console.error("Gọi server thất bại:", response?.error || "Unknown");
                    solving = false;
                }
            });
        } catch (e) {
            console.error("Lỗi gửi ảnh:", e);
            solving = false;
        }
    }

    function dragPiece2D(piece, distanceX, distanceY, initialLeft, initialTop) {
        if (!piece) {
            console.error("Không có mảnh ghép để kéo");
            solving = false;
            return;
        }

        const rect = piece.getBoundingClientRect();
        const startX = rect.left + rect.width / 2;
        const startY = rect.top + rect.height / 2;
        const endX = startX + distanceX;
        const endY = startY + distanceY;

        console.log(`Bắt đầu kéo từ (${startX.toFixed(1)}, ${startY.toFixed(1)}) đến (${endX.toFixed(1)}, ${endY.toFixed(1)})`);

        // Tạo quỹ đạo kéo với easing và nhiễu nhẹ
        const steps = [];
        const numSteps = 90;
        for (let i = 1; i <= numSteps; i++) {
            const t = i / numSteps;
            const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
            const x = startX + distanceX * ease;
            const y = startY + distanceY * ease;
            steps.push({ x: x + (Math.random() * 2 - 1), y: y + (Math.random() * 2 - 1) });
        }

        // Phát sự kiện bắt đầu kéo
        dispatchEvent(piece, 'mouseenter', startX, startY);
        dispatchEvent(piece, 'mousemove', startX, startY);
        dispatchEvent(piece, 'mousedown', startX, startY);
        if (window.PointerEvent) dispatchEvent(piece, 'pointerdown', startX, startY);

        setTimeout(() => {
            let stepIndex = 0;
            const interval = setInterval(() => {
                if (stepIndex < steps.length) {
                    const step = steps[stepIndex];
                    dispatchEvent(document, 'mousemove', step.x, step.y);
                    if (window.PointerEvent) dispatchEvent(document, 'pointermove', step.x, step.y);

                    const progress = (stepIndex + 1) / numSteps;
                    const newLeft = initialLeft + distanceX * progress;
                    const newTop = initialTop + distanceY * progress;
                    piece.style.left = newLeft + 'px';
                    piece.style.top = newTop + 'px';

                    stepIndex++;
                } else {
                    clearInterval(interval);
                    const lastStep = steps[steps.length - 1];
                    dispatchEvent(document, 'mouseup', lastStep.x, lastStep.y);
                    if (window.PointerEvent) dispatchEvent(document, 'pointerup', lastStep.x, lastStep.y);
                    console.log(`Hoàn tất kéo: left=${piece.style.left}, top=${piece.style.top}`);

                    // Click nút xác minh và reset solving sau một khoảng trễ ngắn
                    setTimeout(() => {
                        const verifyBtn = document.querySelector('button.rb6XLo') ||
                            Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('驗證'));

                        if (verifyBtn) {
                            console.log("Phát hiện nút Xác minh, tiến hành click...");

                            // Giả lập click "thật" hơn bằng cách di chuyển chuột đến và nhấn
                            const rect = verifyBtn.getBoundingClientRect();
                            const x = rect.left + rect.width / 2;
                            const y = rect.top + rect.height / 2;

                            dispatchEvent(verifyBtn, 'mouseenter', x, y);
                            dispatchEvent(verifyBtn, 'mousedown', x, y);
                            if (window.PointerEvent) dispatchEvent(verifyBtn, 'pointerdown', x, y);

                            setTimeout(() => {
                                verifyBtn.click();
                                dispatchEvent(verifyBtn, 'mouseup', x, y);
                                if (window.PointerEvent) dispatchEvent(verifyBtn, 'pointerup', x, y);

                                console.log("Đã click nút Xác minh.");

                                // Chờ kết quả phản hồi của captcha rồi mới cho phép giải tiếp
                                setTimeout(() => {
                                    solving = false;
                                    console.log("Sẵn sàng cho captcha tiếp theo.");
                                }, 1500);
                            }, 50);
                        } else {
                            console.warn("Không tìm thấy nút Xác minh (button.rb6XLo)");
                            // Vẫn phải reset solving để observer có thể kích hoạt lại nếu cần
                            setTimeout(() => { solving = false; }, 1000);
                        }
                    }, 800);
                }
            }, 12);
        }, 100);
    }

    function dispatchEvent(el, type, x, y) {
        if (!el) return;
        const common = {
            bubbles: true, cancelable: true, view: window,
            clientX: x, clientY: y, screenX: x, screenY: y,
            buttons: type.includes('up') ? 0 : 1, button: 0, which: type.includes('up') ? 0 : 1
        };
        if (type.startsWith('pointer')) {
            const pe = new PointerEvent(type, {
                ...common,
                pointerId: 1, pointerType: 'mouse', isPrimary: true,
                pressure: type.includes('up') ? 0 : 0.5
            });
            el.dispatchEvent(pe);
        } else {
            el.dispatchEvent(new MouseEvent(type, common));
        }
    }

    // Theo dõi captcha xuất hiện
    const observer = new MutationObserver(() => {
        if (document.querySelector('img.j5BVKH') && !solving) {
            console.log("Phát hiện captcha mới, sẽ giải sau 0.8 giây...");
            setTimeout(solveCaptcha, 800);
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    console.log("Observer đã sẵn sàng (hỗ trợ nhiều captcha liên tiếp).");
})();