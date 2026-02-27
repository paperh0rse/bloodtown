/**
 * Game page – handles all game interactions via WebSocket.
 */
(function () {
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    let ws = null;
    let roomCode = '';
    let playerId = '';
    let playerName = localStorage.getItem('bt_player_name') || '';
    let gameState = null;
    let privateState = null;
    let isHost = false;
    let nightInfoLog = [];
    let selectedTargets = [];
    let currentAction = null;
    let selectablePlayerIds = [];
    let selectMode = '';
    let seatMarks = {};

    const TEAM_ZH = { townsfolk: '村民', outsider: '外来者', minion: '爪牙', demon: '恶魔' };
    const MARK_GROUPS = [
        { label: '阵营', color: '#888', items: ['好人', '坏人'] },
        { label: '村民', color: '#4ea8ee', items: ['洗衣妇', '图书管理员', '调查员', '厨师', '共情者', '占卜师', '掘墓人', '僧侣', '守鸦人', '贞女', '杀手', '士兵', '市长'] },
        { label: '外来者', color: '#56b886', items: ['管家', '酒鬼', '隐士', '圣徒'] },
        { label: '爪牙', color: '#c77dba', items: ['投毒者', '间谍', '猩红女郎', '男爵'] },
        { label: '恶魔', color: '#e05555', items: ['小恶魔'] },
    ];
    const MARK_COLOR_MAP = {};
    MARK_GROUPS.forEach(g => g.items.forEach(it => { MARK_COLOR_MAP[it] = g.color; }));
    MARK_COLOR_MAP['好人'] = '#4ea8ee';
    MARK_COLOR_MAP['坏人'] = '#e05555';

    function roomKey() { return `bt_pid_${roomCode}`; }

    function init() {
        applyHiDPIScale();
        window.addEventListener('resize', applyHiDPIScale);

        const m = location.pathname.match(/\/game\/([A-Z0-9]+)/i);
        if (!m) return;
        roomCode = m[1].toUpperCase();
        playerId = localStorage.getItem(roomKey()) || '';
        $('#room-code-display').textContent = roomCode;

        seatMarks = JSON.parse(localStorage.getItem(`bt_marks_${roomCode}`) || '{}');

        if (!playerName) {
            showJoinForm();
        } else {
            connectWS();
        }

        $('#btn-show-roles').addEventListener('click', openRoleModal);
        $('#btn-close-roles').addEventListener('click', () => $('#role-modal').classList.add('hidden'));
        $$('.help-tab').forEach(tab => tab.addEventListener('click', () => switchHelpTab(tab.dataset.tab)));
        $('#btn-send-chat')?.addEventListener('click', sendChat);
        $('#input-chat')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });
        $('#btn-leave-room')?.addEventListener('click', leaveRoom);

        const nb = $('#notebook');
        if (nb) {
            nb.value = localStorage.getItem(`bt_notes_${roomCode}`) || '';
            nb.addEventListener('input', () => localStorage.setItem(`bt_notes_${roomCode}`, nb.value));
        }
    }

    function applyHiDPIScale() {
        const layout = $('.game-layout');
        if (!layout) return;
        const vh = window.innerHeight;
        if (vh > 1080) {
            layout.style.zoom = vh / 1080;
        } else {
            layout.style.zoom = '';
        }
    }

    function showJoinForm() {
        $('#join-overlay').classList.remove('hidden');
        $('#btn-join-game').addEventListener('click', () => {
            const name = $('#input-player-name').value.trim();
            if (!name) return;
            playerName = name;
            localStorage.setItem('bt_player_name', name);
            $('#join-overlay').classList.add('hidden');
            connectWS();
        });
        $('#input-player-name').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') $('#btn-join-game').click();
        });
    }

    // ---- WebSocket ----

    function connectWS() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        ws = new WS(`${proto}://${location.host}/ws/${roomCode}`);

        ws.on('_open', () => {
            updateConnStatus(true);
            if (playerId) {
                ws.send('reconnect', { player_id: playerId });
            } else {
                ws.send('join', { name: playerName });
            }
        });

        ws.on('_close', () => updateConnStatus(false));

        ws.on('joined', (data) => {
            playerId = data.player_id;
            localStorage.setItem(roomKey(), playerId);
        });

        ws.on('reconnected', () => showToast('已重新连接', 'info'));

        ws.on('reconnect_failed', () => {
            playerId = '';
            localStorage.removeItem(roomKey());
            ws.send('join', { name: playerName });
        });

        ws.on('game_state', (data) => {
            gameState = data;
            isHost = data.host_id === playerId;
            renderAll();
        });

        ws.on('private_state', (data) => { privateState = data; renderPrivate(); });
        ws.on('role_assigned', (data) => { privateState = data; renderPrivate(); });

        ws.on('night_info', (data) => {
            nightInfoLog.push(data);
            renderNightInfo(data);
            setBroadcast(data.message);
        });

        ws.on('night_action', (data) => {
            currentAction = data;
            selectedTargets = [];
            renderNightAction(data);
            setBroadcast(data.prompt);
        });

        ws.on('night_result', (data) => { addLogHTML(fmtLog(data.message), 'death'); setBroadcast(data.message); });
        ws.on('nomination_open', (data) => { addLogHTML(fmtLog(data.message), 'phase'); setBroadcast(data.message); });

        ws.on('nomination', (data) => {
            addLogHTML(`${ptag(data.nominator_seat, data.nominator_name)} 提名了 ${ptag(data.nominee_seat, data.nominee_name)}`, 'event');
            setBroadcast(`[${data.nominator_seat}]${data.nominator_name} 提名了 [${data.nominee_seat}]${data.nominee_name}`);
        });

        ws.on('speech_start', (data) => setBroadcast(data.message));

        ws.on('voting_start', (data) => { renderVoting(data); setBroadcast(data.message); });

        ws.on('vote_result', (data) => {
            addLogHTML(`${ptag(data.nominee_seat, data.nominee_name)}: ${data.yes_votes}票赞成 (需要${data.needed}票)`, 'vote');
            setBroadcast(`[${data.nominee_seat}]${data.nominee_name}: ${data.yes_votes}/${data.needed}票`);
            hideActionPanel();
        });

        ws.on('virgin_trigger', (data) => { addLogHTML(fmtLog(data.message), 'ability'); setBroadcast(data.message); });
        ws.on('execution', (data) => { addLogHTML(fmtLog(data.message), 'death'); setBroadcast(data.message); });
        ws.on('no_execution', (data) => { addLogHTML(fmtLog(data.message), 'event'); setBroadcast(data.message); });
        ws.on('slayer_success', (data) => { addLogHTML(fmtLog(data.message), 'ability'); setBroadcast(data.message); });
        ws.on('slayer_fail', (data) => { addLogHTML(fmtLog(data.message), 'event'); setBroadcast(data.message); });

        ws.on('speech_order', (data) => setBroadcast(data.message));
        ws.on('day_announce', (data) => setBroadcast(data.message));

        ws.on('room_closed', (data) => {
            alert(data.message || '房间已关闭');
            localStorage.removeItem(roomKey());
            location.href = '/';
        });

        ws.on('game_over', (data) => renderGameOver(data));

        ws.on('game_restarted', () => {
            seatMarks = {};
            localStorage.removeItem(`bt_marks_${roomCode}`);
            const overlay = $('#gameover-overlay');
            if (overlay) { overlay.classList.add('hidden'); overlay.innerHTML = ''; }
            privateState = null;
            nightInfoLog = [];
            hideActionPanel();
            hideNightPanel();
            const log = $('#game-log');
            if (log) log.innerHTML = '';
            const ni = $('#night-info-container');
            if (ni) ni.innerHTML = '';
            const cb = $('#chat-box');
            if (cb) cb.innerHTML = '';
            setBroadcast('游戏已重置');
        });

        ws.on('chat', (data) => addChatMessage(data.sender_name, data.text));
        ws.on('end_nom_proposal', (data) => { showToast(data.message, 'info'); setBroadcast(data.message); });
        ws.on('end_nom_rejected', (data) => { addLogHTML(fmtLog(data.message), 'event'); showToast(data.message, 'warning'); });
        ws.on('error', (data) => showToast(data.message, 'danger'));

        ws.connect();
    }

    // ---- Broadcast bar ----

    function setBroadcast(msg) {
        const bar = $('#broadcast-bar');
        if (!bar || !msg) return;
        bar.innerHTML = fmtLog(msg);
        bar.classList.remove('flash');
        void bar.offsetWidth;
        bar.classList.add('flash');
    }

    // ---- Render ----

    function renderAll() {
        if (!gameState) return;
        renderPhase();
        renderTownSquare();
        renderDayPanel();
        renderDayButtons();
        renderHostControls();
        renderPrivate();
        renderLog();
    
    }

    function renderPhase() {
        const banner = $('#phase-banner');
        const phase = gameState.phase;
        const dayNum = gameState.day_number;
        let text = '', cls = '';
        switch (phase) {
            case 'lobby': text = '等待玩家加入...'; break;
            case 'setup': text = '游戏准备中...'; cls = 'night'; break;
            case 'first_night': text = '第一个夜晚'; cls = 'night'; break;
            case 'night': text = `第 ${dayNum} 个夜晚`; cls = 'night'; break;
            case 'day': text = `第 ${dayNum} 天`; cls = 'day'; break;
            case 'game_over': text = '游戏结束'; cls = 'gameover'; break;
            default: text = phase;
        }
        banner.textContent = text;
        banner.className = 'phase-banner ' + cls;
    }

    function renderTownSquare() {
        const sq = $('#town-square');
        if (!sq || !gameState) return;
        sq.innerHTML = '';

        const players = gameState.players;
        const n = players.length;
        if (n === 0) {
            sq.innerHTML = '<div class="center-info">等待玩家加入</div>';
            return;
        }

        const cx = 50, cy = 50, rx = 42, ry = 40;
        players.forEach((p, i) => {
            const angle = (2 * Math.PI * i / n) - Math.PI / 2;
            const x = cx + rx * Math.cos(angle);
            const y = cy + ry * Math.sin(angle);

            const seat = document.createElement('div');
            seat.className = 'seat';
            if (!p.alive) {
                seat.classList.add('dead');
                if (!p.has_vote_token) seat.classList.add('no-vote');
            }
            if (p.id === playerId) seat.classList.add('self');
            if (selectablePlayerIds.includes(p.id)) seat.classList.add('selectable');
            seat.style.left = x + '%';
            seat.style.top = y + '%';
            seat.dataset.playerId = p.id;

            let tags = '';
            if (p.is_bot) tags += '<span class="seat-tag bot">BOT</span>';
            else if (p.id === gameState.host_id) tags += '<span class="seat-tag host">主</span>';

            seat.innerHTML = `
                <span class="seat-number">${i + 1}</span>
                <span class="seat-name">${esc(p.name)}</span>
                ${!p.alive ? '<span class="seat-dead-mark">✕</span>' : ''}
                ${tags}
            `;

            seat.addEventListener('click', () => onSeatClick(p.id));
            sq.appendChild(seat);

            const mark = seatMarks[p.id] || '';
            const markColor = MARK_COLOR_MAP[mark] || '';
            const markEl = document.createElement('div');
            markEl.className = 'seat-mark' + (mark ? ' has-mark' : '');
            markEl.style.left = (x + 4.5) + '%';
            markEl.style.top = (y - 1) + '%';
            if (markColor) { markEl.style.color = markColor; markEl.style.borderColor = markColor; }
            markEl.textContent = mark || '·';
            markEl.title = '点击标注';
            markEl.addEventListener('click', (e) => { e.stopPropagation(); openMarkPicker(p.id, markEl); });
            sq.appendChild(markEl);
        });

        const center = document.createElement('div');
        center.className = 'center-info';
        center.innerHTML = `${n} 人`;
        sq.appendChild(center);
    }

    function renderPrivate() {
        const panel = $('#private-panel');
        if (!panel || !privateState) return;

        const isEvil = privateState.alignment === 'evil';
        panel.innerHTML = `
            <div class="role-card ${isEvil ? 'evil' : 'good'}">
                <div class="role-name">${esc(privateState.role_name)}</div>
                <div class="role-team">${TEAM_ZH[privateState.team] || privateState.team} · ${isEvil ? '邪恶阵营' : '好人阵营'}</div>
                <div class="role-ability">${esc(privateState.role_ability)}</div>
            </div>
        `;

        renderSlayerButton();
    }

    function renderSlayerButton() {
        const area = $('#slayer-btn-area');
        if (!area) return;
        area.innerHTML = '';

        if (!privateState || !gameState) return;
        if (!privateState.alive || gameState.phase !== 'day') return;

        const btn = document.createElement('button');
        btn.className = 'btn btn-danger btn-block btn-sm mt-1';
        btn.textContent = '声称杀手并开枪';
        btn.addEventListener('click', () => enterSlayerMode());
        area.appendChild(btn);
    }

    function renderNightInfo(data) {
        const container = $('#night-info-container');
        if (!container) return;

        const div = document.createElement('div');
        div.className = 'info-panel';
        div.innerHTML = `<div>${fmtLog(data.message)}</div>`;

        if (data.info_type === 'spy_grimoire' && data.grimoire) {
            const table = document.createElement('div');
            table.style.cssText = 'margin-top:0.3rem;font-size:0.72rem;';
            data.grimoire.forEach(g => {
                const row = document.createElement('div');
                row.style.cssText = 'padding:0.1rem 0;display:flex;gap:0.3rem;align-items:center;';
                row.innerHTML = `
                    ${ptag(g.seat, g.name)}
                    <span style="color:${g.alignment === 'evil' ? 'var(--accent-red)' : 'var(--accent-blue)'}">${esc(g.role_name)}</span>
                    ${g.alive ? '' : '<span style="color:var(--text-muted)">(死亡)</span>'}
                `;
                table.appendChild(row);
            });
            div.appendChild(table);
        }

        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
    }

    // ---- Night action panel (NO option list, only prompt + confirm) ----

    function renderNightAction(data) {
        const panel = $('#night-action-panel');
        if (!panel) return;
        panel.classList.remove('hidden');
        panel.innerHTML = '';

        selectMode = 'night';
        setSelectableTargets(data.options || []);

        const title = document.createElement('h3');
        title.textContent = data.prompt;
        panel.appendChild(title);

        const hint = document.createElement('div');
        hint.style.cssText = 'color:var(--text-secondary);font-size:0.72rem;margin-bottom:0.3rem;';
        const chooseCount = data.choose_count || 1;
        hint.textContent = `在圆桌上点击选择 ${chooseCount} 名玩家`;
        panel.appendChild(hint);

        if (data.timeout) {
            const timer = document.createElement('div');
            timer.className = 'timer';
            let remaining = data.timeout;
            timer.textContent = formatTime(remaining);
            const interval = setInterval(() => {
                remaining--;
                timer.textContent = formatTime(remaining);
                if (remaining <= 0) clearInterval(interval);
            }, 1000);
            panel.appendChild(timer);
        }

        const selDisplay = document.createElement('div');
        selDisplay.id = 'night-sel-display';
        selDisplay.style.cssText = 'color:var(--accent-gold);font-size:0.75rem;min-height:1.2rem;margin:0.2rem 0;';
        panel.appendChild(selDisplay);

        const confirmBtn = document.createElement('button');
        confirmBtn.className = 'btn btn-gold btn-block mt-1';
        confirmBtn.textContent = '确认';
        confirmBtn.addEventListener('click', () => {
            if (chooseCount === 1 && selectedTargets.length === 1) {
                ws.send('action_response', { chosen_id: selectedTargets[0] });
                hideNightPanel();
            } else if (chooseCount > 1 && selectedTargets.length === chooseCount) {
                ws.send('action_response', { chosen_ids: selectedTargets });
                hideNightPanel();
            } else {
                showToast(`请选择 ${chooseCount} 名玩家`, 'warning');
            }
        });
        panel.appendChild(confirmBtn);
    }

    function updateNightSelDisplay() {
        const el = $('#night-sel-display');
        if (!el || !gameState) return;
        if (selectedTargets.length === 0) {
            el.textContent = '';
            return;
        }
        const names = selectedTargets.map(id => {
            const p = gameState.players.find(pp => pp.id === id);
            return p ? ptag(p.seat + 1, p.name) : esc(id);
        });
        el.innerHTML = '已选: ' + names.join(', ');
    }

    function setSelectableTargets(options) {
        selectablePlayerIds = options.map(o => o.id);
        renderTownSquare();
    }

    function clearSelectableTargets() {
        selectablePlayerIds = [];
        selectMode = '';
        $$('.town-square .seat.selectable').forEach(s => s.classList.remove('selectable'));
        $$('.town-square .seat.selected').forEach(s => s.classList.remove('selected'));
    }

    function highlightSeatSelection(ids) {
        $$('.town-square .seat.selected').forEach(s => s.classList.remove('selected'));
        ids.forEach(id => {
            const seat = $(`.town-square .seat[data-player-id="${id}"]`);
            if (seat) seat.classList.add('selected');
        });
    }

    function hideNightPanel() {
        const panel = $('#night-action-panel');
        if (panel) { panel.classList.add('hidden'); panel.innerHTML = ''; }
        currentAction = null;
        selectedTargets = [];
        clearSelectableTargets();
    }

    function hideActionPanel() {
        const panel = $('#action-panel');
        if (panel) { panel.classList.add('hidden'); panel.innerHTML = ''; }
    }

    // ---- Day panel ----

    function renderDayPanel() {
        const dayPanel = $('#day-panel');
        if (!dayPanel || !gameState) return;

        const isDay = gameState.phase === 'day';
        const hasRecords = (gameState.nomination_records || []).length > 0;

        if (!isDay && !hasRecords) {
            dayPanel.classList.add('hidden');
            return;
        }
        dayPanel.classList.remove('hidden');

        const nomCtrl = $('#nom-controls');
        if (nomCtrl) {
            nomCtrl.innerHTML = '';
            const sub = isDay ? gameState.day_sub : '';

            if (sub === 'nomination') {
                const info = document.createElement('div');
                info.style.cssText = 'text-align:center;margin-bottom:0.3rem;color:var(--text-secondary);font-size:0.72rem;';
                info.textContent = `剩余提名次数：${gameState.nominations_remaining}`;
                nomCtrl.appendChild(info);

                const endVote = gameState.end_nomination_vote;
                if (endVote) renderEndNomVoteStatus(nomCtrl, endVote);
            } else if (sub === 'nominator_speech' || sub === 'nominee_speech') {
                renderSpeechUI(nomCtrl, sub);
            } else if (sub === 'voting') {
                const info = document.createElement('div');
                info.style.cssText = 'text-align:center;color:var(--accent-gold);font-size:0.75rem;font-weight:600;';
                info.textContent = '投票进行中...';
                nomCtrl.appendChild(info);
            }
        }

        renderNominationRecords();
    }

    function renderDayButtons() {
        const container = $('#day-buttons');
        if (!container || !gameState) return;
        container.innerHTML = '';

        const isDay = gameState.phase === 'day';
        const me = gameState.players.find(p => p.id === playerId);
        if (!isDay || !me || !me.alive) {
            container.classList.add('hidden');
            return;
        }
        container.classList.remove('hidden');
        container.style.display = 'flex';

        const sub = gameState.day_sub;

        if (sub === 'nomination') {
            const nomBtn = document.createElement('button');
            const alreadyNominated = (gameState.nominators_today || []).includes(playerId);
            nomBtn.className = 'btn btn-gold btn-block btn-sm';
            nomBtn.textContent = alreadyNominated ? '已提名过' : '我要提名';
            nomBtn.disabled = alreadyNominated;
            if (!alreadyNominated) nomBtn.addEventListener('click', () => enterNominationMode());
            container.appendChild(nomBtn);

            if (!gameState.end_nomination_vote) {
                const endBtn = document.createElement('button');
                endBtn.className = 'btn btn-secondary btn-block btn-sm';
                endBtn.textContent = '提议结束提名';
                endBtn.addEventListener('click', () => ws.send('propose_end_nominations'));
                container.appendChild(endBtn);
            }
        }
    }

    function enterNominationMode() {
        if (!gameState) return;
        selectMode = 'nominate';
        const alive = gameState.players.filter(p => p.alive && p.id !== playerId);
        selectablePlayerIds = alive.map(p => p.id);
        renderTownSquare();
        setBroadcast('点击圆桌头像选择提名目标');
    }

    function enterSlayerMode() {
        if (!gameState) return;
        selectMode = 'slayer';
        const alive = gameState.players.filter(p => p.alive && p.id !== playerId);
        selectablePlayerIds = alive.map(p => p.id);
        renderTownSquare();
        setBroadcast('点击圆桌头像选择开枪目标');
    }

    function renderNominationRecords() {
        const container = $('#nomination-records');
        if (!container || !gameState) return;
        container.innerHTML = '';

        const records = gameState.nomination_records || [];
        if (records.length === 0) return;

        const table = document.createElement('table');
        table.className = 'nom-table';
        table.innerHTML = `<thead><tr><th>提名者</th><th>被提名</th><th>赞成/总</th><th>赞成者</th><th>结果</th></tr></thead>`;
        const tbody = document.createElement('tbody');
        records.forEach(r => {
            const tr = document.createElement('tr');
            const passClass = r.passed ? 'nom-pass' : 'nom-fail';
            const voterSeats = (r.yes_voter_seats || []).map(s => s + '').join(' ');
            tr.innerHTML = `
                <td>${ptag(r.nominator_seat, r.nominator_name)}</td>
                <td>${ptag(r.nominee_seat, r.nominee_name)}</td>
                <td>${r.yes_votes}票 <span style="font-size:0.55rem;color:var(--text-muted)">(需${r.needed})</span></td>
                <td style="font-size:0.62rem;color:var(--text-secondary)">${voterSeats || '-'}</td>
                <td class="${passClass}">${r.passed ? '通过' : '未通过'}</td>
            `;
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        container.appendChild(table);
    }

    function renderSpeechUI(parent, sub) {
        const speech = gameState.speech;
        if (!speech) return;
        const isNominator = sub === 'nominator_speech';
        const label = isNominator ? '提名者发言' : '被提名者发言';

        const div = document.createElement('div');
        div.style.cssText = 'text-align:center;margin-bottom:0.3rem;';
        div.innerHTML = `
            <div style="color:var(--accent-gold);font-size:0.78rem;font-weight:600;margin-bottom:0.2rem;">${label}</div>
            <div style="color:var(--text-primary);font-size:0.82rem;">${ptag(speech.player_seat, speech.player_name)} 发言中...</div>
        `;
        parent.appendChild(div);

        if (speech.player_id === playerId) {
            const btn = document.createElement('button');
            btn.className = 'btn btn-gold btn-block btn-sm mt-1';
            btn.textContent = '发言完毕';
            btn.addEventListener('click', () => ws.send('speech_done'));
            parent.appendChild(btn);
        }
    }

    function renderVoting(data) {
        const panel = $('#action-panel');
        if (!panel) return;
        if (data.nominator === playerId) return;

        const me = gameState && gameState.players.find(p => p.id === playerId);
        const isDead = me && !me.alive;
        const noLabel = isDead ? '弃权' : '反对';

        panel.classList.remove('hidden');
        panel.innerHTML = `
            <h3>对 ${ptag(data.nominee_seat, data.nominee_name)} 的投票</h3>
            <div class="vote-section">
                <button class="btn btn-success btn-sm" id="btn-vote-yes">赞成处决</button>
                <button class="btn btn-secondary btn-sm" id="btn-vote-no">${noLabel}</button>
            </div>
        `;
        $('#btn-vote-yes').addEventListener('click', () => {
            ws.send('vote', { vote: true });
            showToast('已投赞成票', 'info');
            hideActionPanel();
        });
        $('#btn-vote-no').addEventListener('click', () => {
            ws.send('vote', { vote: false });
            showToast(isDead ? '已弃权' : '已投反对票', 'info');
            hideActionPanel();
        });
    }

    function renderEndNomVoteStatus(parent, endVote) {
        const statusDiv = document.createElement('div');
        statusDiv.className = 'info-panel';
        statusDiv.style.marginBottom = '0.3rem';

        const agreedCount = Object.values(endVote.votes).filter(v => v).length;
        statusDiv.innerHTML = `
            <div style="margin-bottom:0.3rem;font-size:0.72rem;">
                ${ptag(endVote.proposer_seat, endVote.proposer_name)} 提议结束提名
                <span style="color:var(--text-muted);font-size:0.68rem;">(${agreedCount}/${endVote.needed} 同意)</span>
            </div>
        `;

        const me = gameState.players.find(p => p.id === playerId);
        const myVote = endVote.votes[playerId];
        if (me && me.alive && myVote === undefined) {
            const btnRow = document.createElement('div');
            btnRow.style.cssText = 'display:flex;gap:0.3rem;';
            const agreeBtn = document.createElement('button');
            agreeBtn.className = 'btn btn-success btn-sm';
            agreeBtn.style.flex = '1';
            agreeBtn.textContent = '同意';
            agreeBtn.addEventListener('click', () => ws.send('vote_end_nominations', { agree: true }));
            btnRow.appendChild(agreeBtn);
            const rejectBtn = document.createElement('button');
            rejectBtn.className = 'btn btn-danger btn-sm';
            rejectBtn.style.flex = '1';
            rejectBtn.textContent = '拒绝';
            rejectBtn.addEventListener('click', () => ws.send('vote_end_nominations', { agree: false }));
            btnRow.appendChild(rejectBtn);
            statusDiv.appendChild(btnRow);
        } else if (myVote === true) {
            statusDiv.innerHTML += '<div style="color:var(--accent-green);font-size:0.72rem;">你已同意</div>';
        }

        parent.appendChild(statusDiv);
    }

    function renderHostControls() {
        const panel = $('#host-controls');
        if (!panel) return;
        panel.innerHTML = '';

        if (gameState.phase === 'lobby' && isHost) {
            const wrapper = document.createElement('div');
            wrapper.className = 'card';

            const botRow = document.createElement('div');
            botRow.style.cssText = 'display:flex;gap:0.3rem;margin-bottom:0.3rem;';

            const addBotBtn = document.createElement('button');
            addBotBtn.className = 'btn btn-secondary btn-sm';
            addBotBtn.style.flex = '1';
            addBotBtn.textContent = '+1 机器人';
            addBotBtn.disabled = gameState.players.length >= 15;
            addBotBtn.addEventListener('click', () => ws.send('add_bots', { count: 1 }));
            botRow.appendChild(addBotBtn);

            const addManyBtn = document.createElement('button');
            addManyBtn.className = 'btn btn-secondary btn-sm';
            addManyBtn.style.flex = '1';
            const need = Math.max(0, 5 - gameState.players.length);
            addManyBtn.textContent = need > 0 ? `补至5人 (+${need})` : '+4 机器人';
            addManyBtn.disabled = gameState.players.length >= 15;
            addManyBtn.addEventListener('click', () => ws.send('add_bots', { count: need > 0 ? need : 4 }));
            botRow.appendChild(addManyBtn);

            const clearBotBtn = document.createElement('button');
            clearBotBtn.className = 'btn btn-danger btn-sm';
            clearBotBtn.style.flex = '1';
            clearBotBtn.textContent = '清除机器人';
            clearBotBtn.disabled = gameState.players.filter(p => p.is_bot).length === 0;
            clearBotBtn.addEventListener('click', () => ws.send('remove_bots'));
            botRow.appendChild(clearBotBtn);

            wrapper.appendChild(botRow);

            const btn = document.createElement('button');
            btn.className = 'btn btn-primary btn-block btn-sm';
            btn.textContent = `开始游戏 (${gameState.players.length} 人)`;
            btn.disabled = gameState.players.length < 5;
            btn.addEventListener('click', () => ws.send('start_game'));
            wrapper.appendChild(btn);

            panel.appendChild(wrapper);
        }
    }

    function renderGameOver(data) {
        const overlay = $('#gameover-overlay');
        if (!overlay) return;
        overlay.classList.remove('hidden');

        const winnerText = data.winner === 'good' ? '好人阵营获胜！' : '邪恶阵营获胜！';
        const winnerColor = data.winner === 'good' ? 'var(--accent-blue)' : 'var(--accent-red)';

        const goodPlayers = (data.players || []).filter(p => p.alignment === 'good');
        const evilPlayers = (data.players || []).filter(p => p.alignment === 'evil');
        const logs = data.log_summary || [];

        overlay.innerHTML = `
            <div class="card" style="max-width:700px;margin:1.5rem auto;">
                <h2 style="text-align:center;color:${winnerColor};font-size:1.3rem;border:none;">
                    ${winnerText}
                </h2>
                <p style="text-align:center;color:var(--text-secondary);margin-bottom:0.75rem;font-size:0.82rem;">${esc(data.message)}</p>

                <div style="margin-bottom:0.75rem;">
                    <div style="color:var(--accent-gold);font-weight:600;font-size:0.78rem;margin-bottom:0.3rem;">阵营一览</div>
                    <div class="review-teams">
                        <div class="review-team">
                            <div style="color:var(--accent-blue);font-weight:600;font-size:0.75rem;margin-bottom:0.2rem;">好人阵营</div>
                            ${goodPlayers.map(p => `
                                <div class="review-player ${p.alive ? '' : 'dead'}">
                                    ${ptag(p.seat || '?', p.name)}
                                    <span style="color:var(--accent-blue)">${esc(p.role_name)}</span>
                                </div>
                            `).join('')}
                        </div>
                        <div class="review-team">
                            <div style="color:var(--accent-red);font-weight:600;font-size:0.75rem;margin-bottom:0.2rem;">邪恶阵营</div>
                            ${evilPlayers.map(p => `
                                <div class="review-player ${p.alive ? '' : 'dead'}">
                                    ${ptag(p.seat || '?', p.name)}
                                    <span style="color:var(--accent-red)">${esc(p.role_name)}</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                </div>

                ${logs.length > 0 ? `
                <div style="margin-bottom:0.75rem;">
                    <div style="color:var(--accent-gold);font-weight:600;font-size:0.78rem;margin-bottom:0.3rem;">关键事件</div>
                    <div class="review-events">
                        ${logs.map(l => `<div class="review-event">${fmtLog(l)}</div>`).join('')}
                    </div>
                </div>
                ` : ''}

                <div style="display:flex;gap:0.4rem;margin-top:0.75rem;">
                    ${isHost ? '<button class="btn btn-primary" style="flex:1" id="btn-restart">再来一局</button>' : ''}
                    <button class="btn btn-secondary" style="flex:1" onclick="location.href=\'/\'">返回大厅</button>
                </div>
            </div>
        `;
        const restartBtn = overlay.querySelector('#btn-restart');
        if (restartBtn) restartBtn.addEventListener('click', () => ws.send('restart_game'));

        setBroadcast(winnerText);
    }

    // ---- Seat click (unified handler) ----

    function onSeatClick(targetId) {
        if (!gameState) return;

        if (selectMode === 'night' && currentAction) {
            const chooseCount = currentAction.choose_count || 1;
            if (selectablePlayerIds.includes(targetId)) {
                if (chooseCount === 1) {
                    selectedTargets = [targetId];
                } else {
                    const idx = selectedTargets.indexOf(targetId);
                    if (idx >= 0) {
                        selectedTargets.splice(idx, 1);
                    } else if (selectedTargets.length < chooseCount) {
                        selectedTargets.push(targetId);
                    }
                }
                highlightSeatSelection(selectedTargets);
                updateNightSelDisplay();
            }
            return;
        }

        if (selectMode === 'nominate' && selectablePlayerIds.includes(targetId)) {
            const target = gameState.players.find(p => p.id === targetId);
            if (target) showInGameConfirm(`提名 ${ptag(target.seat + 1, target.name)}？`, () => {
                ws.send('nominate', { nominee_id: targetId });
                clearSelectableTargets();
            });
            return;
        }

        if (selectMode === 'slayer' && selectablePlayerIds.includes(targetId)) {
            const target = gameState.players.find(p => p.id === targetId);
            if (target) showInGameConfirm(`对 ${ptag(target.seat + 1, target.name)} 开枪？此操作公开可见。`, () => {
                ws.send('slayer_action', { target_id: targetId });
                clearSelectableTargets();
            });
            return;
        }
    }

    // ---- Role help modal (two-column) ----

    function switchHelpTab(tabId) {
        $$('.help-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
        $$('.help-tab-content').forEach(c => c.classList.add('hidden'));
        const target = $(`#tab-${tabId}`);
        if (target) target.classList.remove('hidden');
        if (tabId === 'dist') renderDistTable();
    }

    async function openRoleModal() {
        const modal = $('#role-modal');
        const content = $('#role-list-content');
        modal.classList.remove('hidden');

        if (content.children.length > 0) return;

        content.innerHTML = '<div style="text-align:center;color:var(--text-muted);">加载中...</div>';
        try {
            const res = await fetch('/api/roles');
            const roles = await res.json();

            const groups = { townsfolk: [], outsider: [], minion: [], demon: [] };
            roles.forEach(r => { if (groups[r.team]) groups[r.team].push(r); });

            function renderItems(list, team) {
                return list.map(r =>
                    `<div class="role-help-item ${team}"><span class="rh-name">${esc(r.name)}</span><span class="rh-ability">${esc(r.ability)}</span></div>`
                ).join('');
            }

            content.innerHTML = `
                <div class="role-modal-grid">
                    <div class="role-col">
                        <h3>村民</h3>
                        ${renderItems(groups.townsfolk, 'townsfolk')}
                    </div>
                    <div class="role-col">
                        <h3>外来者</h3>
                        ${renderItems(groups.outsider, 'outsider')}
                        <h3 style="margin-top:0.5rem;">爪牙</h3>
                        ${renderItems(groups.minion, 'minion')}
                        <h3 style="margin-top:0.5rem;">恶魔</h3>
                        ${renderItems(groups.demon, 'demon')}
                    </div>
                </div>
            `;
        } catch {
            content.innerHTML = '<div style="color:var(--accent-red);">加载失败</div>';
        }
    }

    const PLAYER_DIST = {
        5:  [3, 0, 1, 1],
        6:  [3, 1, 1, 1],
        7:  [5, 0, 1, 1],
        8:  [5, 1, 1, 1],
        9:  [5, 2, 1, 1],
        10: [7, 0, 2, 1],
        11: [7, 1, 2, 1],
        12: [7, 2, 2, 1],
        13: [9, 0, 3, 1],
        14: [9, 1, 3, 1],
        15: [9, 2, 3, 1],
    };

    function renderDistTable() {
        const el = $('#dist-content');
        if (!el || el.children.length > 0) return;

        const currentN = gameState ? gameState.players.length : 0;

        let rows = '';
        for (const [n, [tf, out, min, dem]] of Object.entries(PLAYER_DIST)) {
            const good = tf + out;
            const evil = min + dem;
            const isCurrent = parseInt(n) === currentN;
            const cls = isCurrent ? ' class="dist-current"' : '';
            rows += `<tr${cls}>
                <td>${n}</td>
                <td style="color:var(--accent-blue);font-weight:600;">${good}</td>
                <td>${tf}</td><td>${out}</td>
                <td style="color:var(--accent-red);font-weight:600;">${evil}</td>
                <td>${min}</td><td>${dem}</td>
            </tr>`;
        }

        el.innerHTML = `
            <table class="dist-table">
                <thead>
                    <tr>
                        <th>人数</th>
                        <th style="color:var(--accent-blue);">好人</th>
                        <th>村民</th><th>外来者</th>
                        <th style="color:var(--accent-red);">邪恶</th>
                        <th>爪牙</th><th>恶魔</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:0.5rem;font-size:0.68rem;color:var(--text-muted);">
                * 如果场上有男爵，外来者+2，村民-2
            </div>
        `;
    }

    // ---- In-game confirm dialog ----

    function showInGameConfirm(msg, onConfirm) {
        let existing = $('#ingame-confirm');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'ingame-confirm';
        overlay.className = 'ingame-confirm-overlay';
        overlay.innerHTML = `
            <div class="ingame-confirm-box">
                <div class="ingame-confirm-msg">${msg}</div>
                <div class="ingame-confirm-btns">
                    <button class="btn btn-gold btn-sm" id="ic-yes">确定</button>
                    <button class="btn btn-secondary btn-sm" id="ic-no">取消</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        overlay.querySelector('#ic-yes').addEventListener('click', () => { overlay.remove(); onConfirm(); });
        overlay.querySelector('#ic-no').addEventListener('click', () => { overlay.remove(); clearSelectableTargets(); });
        overlay.addEventListener('click', (e) => { if (e.target === overlay) { overlay.remove(); clearSelectableTargets(); } });
    }

    // ---- Seat mark picker ----

    function openMarkPicker(pid, anchorEl) {
        let existing = $('#mark-picker');
        if (existing) existing.remove();

        const picker = document.createElement('div');
        picker.id = 'mark-picker';
        picker.className = 'mark-picker';

        const clearBtn = document.createElement('button');
        clearBtn.className = 'mark-opt mark-clear' + (!seatMarks[pid] ? ' active' : '');
        clearBtn.textContent = '✕ 清除';
        clearBtn.addEventListener('click', () => {
            delete seatMarks[pid];
            localStorage.setItem(`bt_marks_${roomCode}`, JSON.stringify(seatMarks));
            picker.remove();
            renderTownSquare();
        });
        picker.appendChild(clearBtn);

        MARK_GROUPS.forEach(group => {
            const row = document.createElement('div');
            row.className = 'mark-group';
            const lbl = document.createElement('span');
            lbl.className = 'mark-group-label';
            lbl.textContent = group.label;
            lbl.style.color = group.color;
            row.appendChild(lbl);
            group.items.forEach(opt => {
                const btn = document.createElement('button');
                btn.className = 'mark-opt' + (opt === seatMarks[pid] ? ' active' : '');
                btn.textContent = opt;
                btn.style.borderColor = group.color;
                btn.style.color = group.color;
                btn.addEventListener('click', () => {
                    seatMarks[pid] = opt;
                    localStorage.setItem(`bt_marks_${roomCode}`, JSON.stringify(seatMarks));
                    picker.remove();
                    renderTownSquare();
                });
                row.appendChild(btn);
            });
            picker.appendChild(row);
        });

        const rect = anchorEl.getBoundingClientRect();
        picker.style.position = 'fixed';
        picker.style.left = rect.left + 'px';
        picker.style.top = (rect.bottom + 4) + 'px';
        picker.style.zIndex = '80';
        document.body.appendChild(picker);

        const closePicker = (e) => {
            if (!picker.contains(e.target)) { picker.remove(); document.removeEventListener('mousedown', closePicker); }
        };
        setTimeout(() => document.addEventListener('mousedown', closePicker), 0);
    }

    // ---- Timer ----
    function formatTime(s) {
        const m = Math.floor(s / 60);
        const sec = s % 60;
        return `${m}:${sec.toString().padStart(2, '0')}`;
    }

    // ---- Log ----
    function addLogEntry(message, type = '') {
        const log = $('#game-log');
        if (!log) return;
        const entry = document.createElement('div');
        entry.className = 'log-entry ' + type;
        entry.textContent = message;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }

    function addLogHTML(html, type = '') {
        const log = $('#game-log');
        if (!log) return;
        const entry = document.createElement('div');
        entry.className = 'log-entry ' + type;
        entry.innerHTML = html;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }

    function renderLog() {
        if (!gameState) return;
        const log = $('#game-log');
        if (!log) return;
        const existing = log.children.length;
        const entries = gameState.log || [];
        for (let i = existing; i < entries.length; i++) {
            addLogHTML(fmtLog(entries[i].message), entries[i].type);
        }
    }

    // ---- Chat (separate from log) ----
    function addChatMessage(name, text) {
        const box = $('#chat-box');
        if (!box) return;
        const msg = document.createElement('div');
        msg.className = 'chat-msg';
        msg.innerHTML = `<span class="chat-name">${esc(name)}</span> ${esc(text)}`;
        box.appendChild(msg);
        box.scrollTop = box.scrollHeight;
    }

    function sendChat() {
        const input = $('#input-chat');
        if (!input || !input.value.trim()) return;
        ws.send('chat', { text: input.value.trim() });
        input.value = '';
    }

    function leaveRoom() {
        showInGameConfirm('确定退出房间？', () => {
            if (ws) ws.send('leave');
            localStorage.removeItem(roomKey());
            if (ws) ws.close();
            location.href = '/';
        });
    }

    // ---- Connection status ----
    function updateConnStatus(connected) {
        const el = $('#conn-status');
        if (!el) return;
        el.className = 'conn-status ' + (connected ? 'connected' : 'disconnected');
        el.textContent = connected ? '已连接' : '连接断开';
    }

    // ---- Toast ----
    function showToast(message, type = 'info') {
        const container = $('#toast-container');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `info-panel ${type}`;
        toast.textContent = message;
        toast.style.animation = 'fadeIn 0.3s';
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 4000);
    }

    // ---- Util ----
    function esc(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function ptag(seat, name) {
        return `<span class="ptag"><span class="ptag-seat">${seat}</span>${esc(name)}</span>`;
    }

    function fmtLog(text) {
        return esc(text || '').replace(/\[(\d+)\]([^\s\[,，。、；：]+)/g,
            (_, seat, name) => `<span class="ptag"><span class="ptag-seat">${seat}</span>${name}</span>`);
    }

    init();
})();
