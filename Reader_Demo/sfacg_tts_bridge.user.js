// ==UserScript==
// @name         SF轻小说 → 本地 TTS 朗读
// @namespace    sfacg-tts-bridge
// @version      0.1
// @description  提取SF轻小说(菠萝包)章节正文，发送到本地 Python 桥接服务 (127.0.0.1:8899) 进行 TTS 朗读
// @author       you
// @match        https://book.sfacg.com/Novel/*/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @license      MIT
// ==/UserScript==

(function() {
    'use strict';

    const BRIDGE_URL = 'http://127.0.0.1:8899';

    function extractContent() {
        const body = document.getElementById('ChapterBody');
        if (!body) return null;

        const paras = body.querySelectorAll('p');
        const lines = [];
        paras.forEach(p => {
            let t = p.textContent.replace(/[\t\r]+/g, ' ').trim();
            t = t.replace(/\s*\d{1,3}$/, '').trim();
            if (t && !/^\d+$/.test(t)) lines.push(t);
        });
        return lines.join('\n');
    }

    function extractTitle() {
        const el = document.querySelector('h1.article-title');
        if (el) return el.textContent.trim();

        // fallback
        let t = document.title || '';
        t = t.replace(/\s*[-–—|]\s*小说全文阅读.*$/, '')
             .replace(/\s*[-–—|]\s*SF轻小说.*$/, '')
             .trim();
        return t;
    }

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

    function showToast(msg, type) {
        const old = document.getElementById('sfacg-tts-toast');
        if (old) old.remove();

        const d = document.createElement('div');
        d.id = 'sfacg-tts-toast';
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

    function createButton() {
        if (document.getElementById('btnTtsBridge')) return;

        // 挂在左侧浮动工具栏 #leftFloatBar 里
        const bar = document.getElementById('ctrlBtnArea');
        if (!bar) return;

        const a = document.createElement('a');
        a.id = 'btnTtsBridge';
        a.href = 'javascript:void(0)';
        a.className = 'ctrl-btn';
        a.title = '发送到本地TTS朗读';
        a.textContent = '🎧';
        a.style.cssText = 'font-size:18px; line-height:44px; text-align:center; background:#2d5a2d;';

        a.addEventListener('click', async function(e) {
            e.preventDefault();
            a.textContent = '⏳';
            a.style.background = '#888';

            const text = extractContent();
            if (!text || text.length < 30) {
                showToast('未提取到正文，请确认在章节阅读页', 'error');
                a.textContent = '🎧';
                a.style.background = '#2d5a2d';
                return;
            }

            const title = extractTitle();
            try {
                const r = await sendToBridge(text, title);
                showToast('已发送: ' + (title || '章节'), 'success');
            } catch (e) {
                showToast(e.message, 'error');
            }
            a.textContent = '🎧';
            a.style.background = '#2d5a2d';
        });

        bar.appendChild(a);
    }

    function waitForPage(retries) {
        retries = retries || 30;
        const body = document.getElementById('ChapterBody');
        const bar = document.getElementById('ctrlBtnArea');
        if (body && bar) {
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

    // SPA 导航感知
    let lastUrl = location.href;
    new MutationObserver(() => {
        if (location.href !== lastUrl) {
            lastUrl = location.href;
            const old = document.getElementById('btnTtsBridge');
            if (old) old.remove();
            waitForPage();
        }
    }).observe(document.documentElement, { childList: true, subtree: true });
})();
