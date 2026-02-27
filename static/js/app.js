/**
 * Lobby / index page logic.
 */
(function () {
    const $ = (sel) => document.querySelector(sel);

    // --- Name management ---

    function getPlayerName() { return localStorage.getItem('bt_player_name') || ''; }

    function setPlayerName(name) {
        localStorage.setItem('bt_player_name', name);
        refreshNameBar();
    }

    function refreshNameBar() {
        const bar = $('#name-bar');
        const display = $('#name-display');
        const name = getPlayerName();
        if (bar && display && name) {
            display.textContent = name;
            bar.classList.remove('hidden');
        }
    }

    function showNameModal(force) {
        const modal = $('#name-modal');
        const input = $('#modal-name-input');
        if (!modal || !input) return;

        input.value = getPlayerName();
        modal.classList.remove('hidden');

        if (!force) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal && getPlayerName()) modal.classList.add('hidden');
            });
        }

        function confirm() {
            const name = input.value.trim();
            if (!name) { input.focus(); input.style.borderColor = 'var(--accent-red)'; return; }
            input.style.borderColor = '';
            setPlayerName(name);
            modal.classList.add('hidden');
        }

        $('#modal-name-ok')?.addEventListener('click', confirm);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') confirm(); });
    }

    if (!getPlayerName()) {
        showNameModal(true);
    } else {
        refreshNameBar();
    }

    $('#btn-edit-name')?.addEventListener('click', () => showNameModal(false));

    function ensureName() {
        if (getPlayerName()) return true;
        showNameModal(true);
        return false;
    }

    // --- Create room ---
    $('#btn-create')?.addEventListener('click', async () => {
        if (!ensureName()) return;
        const res = await fetch('/api/rooms', { method: 'POST' });
        const data = await res.json();
        if (data.room_code) {
            window.location.href = `/game/${data.room_code}`;
        }
    });

    // --- Join room ---
    $('#btn-join')?.addEventListener('click', () => {
        if (!ensureName()) return;
        const code = $('#input-room-code')?.value?.trim().toUpperCase();
        if (code && code.length >= 4) {
            window.location.href = `/game/${code}`;
        }
    });

    $('#input-room-code')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') $('#btn-join')?.click();
    });

    // --- Room list ---

    const PHASE_ZH = {
        lobby: '等待中', setup: '准备中', first_night: '首夜',
        night: '夜晚', day: '白天', game_over: '已结束',
    };

    async function loadRoomList() {
        const el = $('#room-list');
        if (!el) return;
        try {
            const res = await fetch('/api/rooms');
            const rooms = await res.json();
            if (rooms.length === 0) {
                el.textContent = '暂无房间，创建一个吧。';
                return;
            }
            el.innerHTML = '';
            const table = document.createElement('table');
            table.style.cssText = 'width:100%;border-collapse:collapse;font-size:0.85rem;';
            table.innerHTML = '<thead><tr style="color:var(--text-muted);font-size:0.75rem;text-align:left;"><th style="padding:0.3rem">房间码</th><th style="padding:0.3rem">状态</th><th style="padding:0.3rem">人数</th><th style="padding:0.3rem"></th></tr></thead>';
            const tbody = document.createElement('tbody');
            rooms.forEach(r => {
                const tr = document.createElement('tr');
                tr.style.borderBottom = '1px solid var(--border-color)';
                const phase = PHASE_ZH[r.phase] || r.phase;
                tr.innerHTML = `
                    <td style="padding:0.3rem;color:var(--accent-gold);font-weight:600;letter-spacing:1px;">${r.code}</td>
                    <td style="padding:0.3rem;">${phase}</td>
                    <td style="padding:0.3rem;">${r.player_count} 人</td>
                    <td style="padding:0.3rem;"><button class="btn btn-gold btn-sm" data-code="${r.code}">加入</button></td>
                `;
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            el.appendChild(table);
            el.querySelectorAll('button[data-code]').forEach(btn => {
                btn.addEventListener('click', () => {
                    if (!ensureName()) return;
                    window.location.href = `/game/${btn.dataset.code}`;
                });
            });
        } catch {
            el.textContent = '加载失败';
        }
    }

    loadRoomList();
    setInterval(loadRoomList, 5000);
})();
