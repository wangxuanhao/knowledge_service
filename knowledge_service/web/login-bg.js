/* ============================================================================
   login-bg.js —— 登录页粒子网络背景（原生 Canvas，零依赖）
   ----------------------------------------------------------------------------
   在整屏画布上渲染「知识图谱」氛围的动态背景，配色贴合系统主题（深墨青 + 青色）：
     · 更多粒子：密度约每 6500px² 一颗（90–260 区间），自适应屏幕；
     · 三层景深：背景层是柔和扩散的大光斑、中景是普通连线节点、前景是快速闪烁小星点；
     · 少量"核心节点"带外圈辉光，模拟图谱里的关键节点；
     · 若干大型大气光斑缓慢漂移，增加高级光感与深度；
     · 距离近的两点连出半透明青色线，形成网状结构；
     · 鼠标移动产生跟随的青色光晕（光影）。
   兼容 devicePixelRatio，窗口缩放自动重建。不加载工作台业务代码。
   ============================================================================ */
(function () {
    'use strict';

    var canvas = document.getElementById('login-canvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');

    var W = 0, H = 0, DPR = 1;
    var parts = [];      // 粒子（分层）
    var orbs = [];       // 大气光斑
    var LINK_DIST = 150; // 连线阈值（CSS 像素）
    var mouse = { x: -1e4, y: -1e4, active: false };

    function rand(a, b) { return a + Math.random() * (b - a); }

    function resize() {
        DPR = Math.min(window.devicePixelRatio || 1, 2);
        W = Math.floor(window.innerWidth * DPR);
        H = Math.floor(window.innerHeight * DPR);
        canvas.width = W;
        canvas.height = H;
        canvas.style.width = window.innerWidth + 'px';
        canvas.style.height = window.innerHeight + 'px';
        spawn();
    }

    function spawn() {
        var px = window.innerWidth * window.innerHeight;       // CSS 平方像素
        var total = Math.round(Math.max(90, Math.min(260, px / 6500)));
        parts = [];
        for (var i = 0; i < total; i++) {
            var t = Math.random();
            var layer = t < 0.16 ? 'back' : (t < 0.62 ? 'mid' : 'front');
            var cfg = layer === 'back'
                ? { r: rand(2.2, 4.5), sp: rand(0.06, 0.14), hot: false }
                : layer === 'mid'
                ? { r: rand(1.0, 2.2), sp: rand(0.12, 0.30), hot: Math.random() < 0.12 }
                : { r: rand(0.6, 1.4), sp: rand(0.28, 0.5), hot: false };
            parts.push({
                layer: layer,
                x: Math.random() * W,
                y: Math.random() * H,
                vx: (Math.random() - 0.5) * 2 * cfg.sp * DPR,
                vy: (Math.random() - 0.5) * 2 * cfg.sp * DPR,
                r: cfg.r * DPR,
                hot: cfg.hot,
                tw: Math.random() * Math.PI * 2
            });
        }

        // 大型大气光斑：极淡、缓慢漂移，制造景深与高级光感
        orbs = [];
        var orbCount = 6;
        for (var k = 0; k < orbCount; k++) {
            orbs.push({
                x: Math.random() * W,
                y: Math.random() * H,
                r: rand(180, 360) * DPR,
                vx: rand(-0.06, 0.06) * DPR,
                vy: rand(-0.06, 0.06) * DPR,
                al: rand(0.05, 0.12),
                hue: Math.random() < 0.5,       // 交替淡青 / 青色
                tw: Math.random() * Math.PI * 2
            });
        }
    }

    function step(t) {
        ctx.clearRect(0, 0, W, H);

        // 1) 大气光斑（最底层，景深氛围）
        var k, o, pa, g, col;
        for (k = 0; k < orbs.length; k++) {
            o = orbs[k];
            o.x += o.vx; o.y += o.vy;
            if (o.x < -o.r || o.x > W + o.r) o.vx *= -1;
            if (o.y < -o.r || o.y > H + o.r) o.vy *= -1;
            pa = o.al * (0.7 + 0.3 * Math.sin(t * 0.0006 + o.tw));
            col = o.hue ? '34,211,238' : '45,212,191';
            g = ctx.createRadialGradient(o.x, o.y, 0, o.x, o.y, o.r);
            g.addColorStop(0, 'rgba(' + col + ',' + pa.toFixed(3) + ')');
            g.addColorStop(1, 'rgba(' + col + ',0)');
            ctx.fillStyle = g;
            ctx.beginPath(); ctx.arc(o.x, o.y, o.r, 0, Math.PI * 2); ctx.fill();
        }

        // 2) 连边：中景 / 前景节点之间
        var i, j, a, b, dx, dy, d2, dist, alpha;
        for (i = 0; i < parts.length; i++) {
            a = parts[i];
            if (a.layer === 'back') continue;
            for (j = i + 1; j < parts.length; j++) {
                b = parts[j];
                if (b.layer === 'back') continue;
                dx = a.x - b.x; dy = a.y - b.y;
                d2 = dx * dx + dy * dy;
                if (d2 < LINK_DIST * LINK_DIST) {
                    dist = Math.sqrt(d2);
                    alpha = (1 - dist / LINK_DIST) * 0.34;
                    ctx.strokeStyle = 'rgba(45, 212, 191, ' + alpha.toFixed(3) + ')';
                    ctx.lineWidth = 1 * DPR;
                    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
                }
            }
        }

        // 3) 节点：按层绘制（呼吸脉动）
        var p, rad, pulseA, g2;
        for (i = 0; i < parts.length; i++) {
            p = parts[i];
            p.x += p.vx; p.y += p.vy;
            if (p.x < -20 || p.x > W + 20) p.vx *= -1;
            if (p.y < -20 || p.y > H + 20) p.vy *= -1;
            pulseA = 0.6 + 0.4 * Math.sin(t * 0.0012 + p.tw);
            rad = p.r * (0.85 + 0.4 * pulseA);

            if (p.layer === 'back') {
                // 背景层：扩散柔和光斑 + 淡心
                g2 = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rad * 4);
                g2.addColorStop(0, 'rgba(94, 234, 212, ' + (0.10 * pulseA).toFixed(3) + ')');
                g2.addColorStop(1, 'rgba(94, 234, 212, 0)');
                ctx.fillStyle = g2;
                ctx.beginPath(); ctx.arc(p.x, p.y, rad * 4, 0, Math.PI * 2); ctx.fill();
                ctx.fillStyle = 'rgba(165, 243, 252, ' + (0.35 * pulseA).toFixed(3) + ')';
                ctx.beginPath(); ctx.arc(p.x, p.y, rad, 0, Math.PI * 2); ctx.fill();
            } else if (p.hot) {
                // 核心节点：外圈青色辉光
                g2 = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rad * 5);
                g2.addColorStop(0, 'rgba(34, 211, 238, ' + (0.28 * pulseA).toFixed(3) + ')');
                g2.addColorStop(1, 'rgba(34, 211, 238, 0)');
                ctx.fillStyle = g2;
                ctx.beginPath(); ctx.arc(p.x, p.y, rad * 5, 0, Math.PI * 2); ctx.fill();
                ctx.fillStyle = 'rgba(103, 232, 249, ' + (0.7 + 0.3 * pulseA).toFixed(3) + ')';
                ctx.beginPath(); ctx.arc(p.x, p.y, rad, 0, Math.PI * 2); ctx.fill();
            } else {
                ctx.fillStyle = 'rgba(94, 234, 212, ' + (0.45 + 0.35 * pulseA).toFixed(3) + ')';
                ctx.beginPath(); ctx.arc(p.x, p.y, rad, 0, Math.PI * 2); ctx.fill();
            }
        }

        // 4) 跟随鼠标的青色光晕（光影）
        if (mouse.active) {
            var mx = mouse.x * DPR, my = mouse.y * DPR;
            var rg = ctx.createRadialGradient(mx, my, 0, mx, my, 300 * DPR);
            rg.addColorStop(0, 'rgba(34, 211, 238, 0.18)');
            rg.addColorStop(1, 'rgba(34, 211, 238, 0)');
            ctx.fillStyle = rg;
            ctx.fillRect(0, 0, W, H);
        }

        requestAnimationFrame(step);
    }

    function onMove(e) { mouse.x = e.clientX; mouse.y = e.clientY; mouse.active = true; }
    function onLeave() { mouse.active = false; }

    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMove, { passive: true });
    window.addEventListener('mouseleave', onLeave);

    resize();
    requestAnimationFrame(step);
})();
