// ==UserScript==
// @name         起点小说 → 本地 TTS 朗读
// @namespace    qidian-tts-bridge
// @version      0.2
// @description  提取起点章节正文，发送到本地 Python 桥接服务 (127.0.0.1:8899) 进行 TTS 朗读
// @author       you
// @match        https://www.qidian.com/chapter/*
// @match        https://read.qidian.com/chapter/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @license      MIT
// ==/UserScript==

(function() {
    'use strict';

    const BRIDGE_URL = 'http://127.0.0.1:8899';

    // ── 正文提取（起点当前 DOM 结构：<main> > <p>） ──

    function extractContent() {
        const main = document.querySelector('main');
        if (!main) return null;

        const paras = main.querySelectorAll('p');
        const lines = [];
        paras.forEach(p => {
            let t = p.textContent.replace(/[\t\r]+/g, ' ').trim();
            // 去掉末尾段落编号（可能跟在文字/标点/引号后面，中间不一定有空格）
            // 例："…所得。"2  → "…所得。"  /  "…二月2" → "…二月"
            t = t.replace(/\s*\d{1,3}$/, '').trim();
            if (t && !/^\d+$/.test(t)) lines.push(t);
        });
        return lines.join('\n');
    }

    function extractTitle() {
        // .title 元素里章节名是文本节点
        const el = document.querySelector('.title');
        if (!el) {
            // fallback: 从 document.title 解析
            let t = document.title || '';
            t = t.replace(/\s*[-–—|]\s*起点中文网.*$/, '')
                 .replace(/\s*[-–—|]\s*.*阅文.*$/, '')
                 .trim();
            return t;
        }
        // 取 .title 下所有纯文本节点的内容
        let title = '';
        el.childNodes.forEach(n => {
            if (n.nodeType === 3) title += n.textContent;
        });
        return title.trim() || el.textContent.trim();
    }

    // ── 发送 ──

    function sendToBridge(text, title) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: 'POST',
                url: BRIDGE_URL,
                headers: { 'Content-Type': 'application/json' },
                data: JSON.stringify({ text: text, title: title, speed: 1.0 }),
                timeout: 5000,
                onload: function(resp) {
                    if (resp.status === 200) {
                        resolve(JSON.parse(resp.responseText));
                    } else {
                        reject(new Error('HTTP ' + resp.status));
                    }
                },
                onerror: function() {
                    reject(new Error('连接失败 — 桥接服务未启动？(127.0.0.1:8899)'));
                },
                ontimeout: function() {
                    reject(new Error('连接超时'));
                }
            });
        });
    }

    // ── Toast ──

    function showToast(msg, type) {
        const old = document.getElementById('qidian-tts-toast');
        if (old) old.remove();

        const d = document.createElement('div');
        d.id = 'qidian-tts-toast';
        d.textContent = msg;
        Object.assign(d.style, {
            position: 'fixed', top: '24px', left: '50%', transform: 'translateX(-50%)',
            zIndex: '999999', padding: '12px 28px', borderRadius: '8px',
            color: '#fff', fontSize: '14px', fontFamily: '"Microsoft YaHei", sans-serif',
            boxShadow: '0 4px 16px rgba(0,0,0,0.35)',
            background: type === 'success' ? '#2d7d2d' : '#c0392b',
            transition: 'opacity 0.3s',
        });
        document.body.appendChild(d);
        setTimeout(() => { d.style.opacity = '0'; setTimeout(() => d.remove(), 300); }, 3000);
    }

    // ── 按钮：挂在起点右侧 #r-menu 里（和别人的"听书"按钮同一区域） ──

    function createButton() {
        if (document.getElementById('btnTtsBridge')) return;

        const rMenu = document.getElementById('r-menu');
        if (!rMenu) return;

        const wrapper = document.createElement('div');
        wrapper.setAttribute('data-v-6cdbc58a', '');
        wrapper.setAttribute('data-v-47ffe1e', '');
        wrapper.className = 'tooltip-wrapper relative flex';
        wrapper.style.marginBottom = '10px';

        wrapper.innerHTML = `
            <a id="btnTtsBridge" data-v-47ffe1ec target="#" href="javascript:void(0)">
                <button data-v-47ffe1ec class="w-64px h-64px flex flex-col items-center justify-center rounded-8px bg-sheet-b-gray-50 text-s-gray-900 noise-bg group hover:bg-sheet-b-bw-white hover:text-primary-red-500 hover:bg-none">
                    <span class="icon-audio text-24px"></span>
                    <span class="text-bo4 text-s-gray-500 mt-2px group-hover:text-primary-red-500" style="font-weight:600;">AI朗读</span>
                </button>
            </a>`;

        rMenu.prepend(wrapper);

        wrapper.querySelector('a').addEventListener('click', async function(e) {
            e.preventDefault();
            const btn = this.querySelector('button');
            const label = btn.querySelector('span:last-child');
            const origText = label.textContent;
            label.textContent = '发送中...';

            const text = extractContent();
            if (!text || text.length < 30) {
                showToast('未提取到正文，请确认在章节阅读页', 'error');
                label.textContent = origText;
                return;
            }

            const title = extractTitle();
            try {
                const r = await sendToBridge(text, title);
                showToast('已发送: ' + (title || '章节'), 'success');
            } catch (e) {
                showToast(e.message, 'error');
            }
            label.textContent = origText;
        });
    }

    // ── 启动：等 <main> 和 #r-menu 都渲染出来 ──

    function waitForPage(retries) {
        retries = retries || 30;
        const main = document.querySelector('main');
        const rMenu = document.getElementById('r-menu');
        if (main && rMenu) {
            createButton();
            return;
        }
        if (retries > 0) {
            setTimeout(() => waitForPage(retries - 1), 500);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => waitForPage());
    } else {
        waitForPage();
    }

    // SPA 导航：起点切章节用 pushState，页面不刷新
    let lastUrl = location.href;
    new MutationObserver(() => {
        if (location.href !== lastUrl) {
            lastUrl = location.href;
            const old = document.getElementById('btnTtsBridge');
            if (old) old.parentElement.remove();
            waitForPage();
        }
    }).observe(document.documentElement, { childList: true, subtree: true });
})();
