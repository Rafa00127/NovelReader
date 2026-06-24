// ==UserScript==
// @name         番茄小说 → 本地 TTS 朗读
// @namespace    fanqie-tts-bridge
// @version      0.1
// @description  提取番茄小说章节正文，发送到本地 Python 桥接服务 (127.0.0.1:8899) 进行 TTS 朗读
// @author       you
// @match        *://fanqienovel.com/reader/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @license      MIT
// ==/UserScript==

(function() {
    'use strict';

    const BRIDGE_URL = 'http://127.0.0.1:8899';

    // ── 番茄小说正文解密 (字符码点映射) ──
    const CODE_ST = 58344, CODE_ED = 58715;
    const CHARSET = [
        "D","在","主","特","家","军","然","表","场","4","要","只","v","和","?","6",
        "别","还","g","现","儿","岁","?","?","此","象","月","3","出","战","工","相",
        "o","男","直","失","世","F","都","平","文","什","V","O","将","真","T","那",
        "当","?","会","立","些","u","是","十","张","学","气","大","爱","两","命",
        "全","后","东","性","通","被","1","它","乐","接","而","感","车","山","公",
        "了","常","以","何","可","话","先","p","i","叫","轻","M","士","w","着",
        "变","尔","快","l","个","说","少","色","里","安","花","远","7","难","师",
        "放","t","报","认","面","道","S","?","克","地","度","I","好","机","U",
        "民","写","把","万","同","水","新","没","书","电","吃","像","斯","5","为",
        "y","白","几","日","教","看","但","第","加","候","作","上","拉","住","有",
        "法","r","事","应","位","利","你","声","身","国","问","马","女","他","Y",
        "比","父","x","A","H","N","s","X","边","美","对","所","金","活","回","意",
        "到","z","从","j","知","又","内","因","点","Q","三","定","8","R","b",
        "正","或","夫","向","德","听","更","?","得","告","并","本","q","过","记",
        "L","让","打","f","人","就","者","去","原","满","体","做","经","K","走",
        "如","孩","c","G","给","使","物","?","最","笑","部","?","员","等","受",
        "k","行","一","条","果","动","光","门","头","见","往","自","解","成","处",
        "天","能","于","名","其","发","总","母","的","死","手","入","路","进","心",
        "来","h","时","力","多","开","已","许","d","至","由","很","界","n","小",
        "与","Z","想","代","么","分","生","口","再","妈","望","次","西","风","种",
        "带","J","?","实","情","才","这","?","E","我","神","格","长","觉","间",
        "年","眼","无","不","亲","关","结","0","友","信","下","却","重","己","老",
        "2","音","字","m","呢","明","之","前","高","P","B","目","太","e","9",
        "起","稜","她","也","W","用","方","子","英","每","理","便","四","数","期",
        "中","C","外","样","a","海","们","任"
    ];

    function decodeChar(cc) {
        const bias = cc - CODE_ST;
        if (bias < 0 || bias >= CHARSET.length || CHARSET[bias] === "?") {
            return String.fromCharCode(cc);
        }
        return CHARSET[bias];
    }

    function decodeText(text) {
        let out = "";
        for (let i = 0; i < text.length; i++) {
            const cc = text.charCodeAt(i);
            out += (cc >= CODE_ST && cc <= CODE_ED) ? decodeChar(cc) : text.charAt(i);
        }
        return out;
    }

    // ── 正文提取 ──

    function extractContent() {
        const el = document.querySelector('.muye-reader-content');
        if (!el) return null;

        // 去掉 VIP 遮罩和隐藏元素
        const clone = el.cloneNode(true);
        clone.querySelectorAll(
            '.muye-to-vip, .muye-to-fanqie, script, style, ' +
            '[style*="display:none"], [style*="display: none"]'
        ).forEach(n => n.remove());

        const paras = clone.querySelectorAll('p');
        if (paras.length > 0) {
            const lines = [];
            paras.forEach(p => {
                let t = decodeText(p.textContent).replace(/[\t\r]+/g, ' ').trim();
                t = t.replace(/\s*\d{1,3}$/, '').trim();
                if (t && !/^\d+$/.test(t)) lines.push(t);
            });
            return lines.join('\n');
        }

        // fallback: 直接取文本 + 解码
        let text = decodeText(clone.textContent || '');
        text = text.replace(/[\t\r]+/g, '').replace(/\n{3,}/g, '\n\n').trim();
        return text;
    }

    function extractTitle() {
        const el = document.querySelector('.muye-reader-title') ||
                    document.querySelector('#heading_id_2');
        if (el) return decodeText(el.textContent.trim());

        const nav = document.querySelector('.muye-reader-nav-title');
        if (nav) {
            let t = decodeText(nav.textContent.trim());
            const sub = document.querySelector('.muye-reader-title');
            return sub ? t + ' - ' + decodeText(sub.textContent.trim()) : t;
        }

        let t = document.title || '';
        t = t.replace(/\s*[-–—|]\s*番茄小说.*$/, '').trim();
        return decodeText(t);
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
        const old = document.getElementById('fanqie-tts-toast');
        if (old) old.remove();

        const d = document.createElement('div');
        d.id = 'fanqie-tts-toast';
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

        const btn = document.createElement('div');
        btn.id = 'btnTtsBridge';
        btn.textContent = '🎧 TTS朗读';
        Object.assign(btn.style, {
            position: 'fixed', top: '100px', right: '20px', zIndex: '9999',
            background: '#f44c4c', color: '#fff', padding: '10px 18px',
            borderRadius: '8px', cursor: 'pointer', fontSize: '14px',
            fontWeight: 'bold', boxShadow: '0 2px 12px rgba(0,0,0,0.3)',
            fontFamily: '"Microsoft YaHei", sans-serif', userSelect: 'none',
        });

        btn.addEventListener('click', async () => {
            btn.textContent = '⏳ 发送中...';
            btn.style.background = '#888';
            btn.style.pointerEvents = 'none';

            const text = extractContent();
            if (!text || text.length < 30) {
                showToast('未提取到正文，可能为VIP章节或页面未加载完', 'error');
                btn.textContent = '🎧 TTS朗读';
                btn.style.background = '#f44c4c';
                btn.style.pointerEvents = 'auto';
                return;
            }

            const title = extractTitle();
            try {
                const r = await sendToBridge(text, title);
                showToast('已发送: ' + (title || '章节'), 'success');
            } catch (e) {
                showToast(e.message, 'error');
            }
            btn.textContent = '🎧 TTS朗读';
            btn.style.background = '#f44c4c';
            btn.style.pointerEvents = 'auto';
        });

        document.body.appendChild(btn);
    }

    function waitForPage(retries) {
        retries = retries || 30;
        const el = document.querySelector('.muye-reader-content');
        if (el) {
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

    // SPA 导航
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
