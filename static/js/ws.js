/**
 * WebSocket wrapper with auto-reconnect.
 */
class WS {
    constructor(url) {
        this._url = url;
        this._ws = null;
        this._handlers = {};
        this._reconnectTimer = null;
        this._reconnectDelay = 1000;
        this._heartbeatTimer = null;
        this._pongTimeout = null;
        this._awaitingPong = false;
        this.connected = false;
    }

    connect() {
        if (this._ws) return;
        this._ws = new WebSocket(this._url);

        this._ws.onopen = () => {
            this.connected = true;
            this._reconnectDelay = 1000;
            this._awaitingPong = false;
            this._startHeartbeat();
            this._dispatch('_open', {});
        };

        this._ws.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                if (msg.type === 'pong') {
                    this._awaitingPong = false;
                    clearTimeout(this._pongTimeout);
                    return;
                }
                this._awaitingPong = false;
                clearTimeout(this._pongTimeout);
                this._dispatch(msg.type, msg.data || {});
            } catch (err) {
                console.error('WS parse error:', err);
            }
        };

        this._ws.onclose = () => {
            this.connected = false;
            this._ws = null;
            this._stopHeartbeat();
            this._dispatch('_close', {});
            this._scheduleReconnect();
        };

        this._ws.onerror = () => {
            this._ws?.close();
        };
    }

    _startHeartbeat() {
        this._stopHeartbeat();
        this._heartbeatTimer = setInterval(() => {
            if (this._ws && this._ws.readyState === WebSocket.OPEN) {
                this._awaitingPong = true;
                this.send('ping');
                this._pongTimeout = setTimeout(() => {
                    if (this._awaitingPong) {
                        console.warn('WS: pong timeout, forcing reconnect');
                        this._ws?.close();
                    }
                }, 5000);
            }
        }, 10000);
    }

    _stopHeartbeat() {
        clearInterval(this._heartbeatTimer);
        this._heartbeatTimer = null;
        clearTimeout(this._pongTimeout);
        this._pongTimeout = null;
        this._awaitingPong = false;
    }

    send(type, data = {}) {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send(JSON.stringify({ type, data }));
        }
    }

    on(type, handler) {
        if (!this._handlers[type]) this._handlers[type] = [];
        this._handlers[type].push(handler);
    }

    _dispatch(type, data) {
        (this._handlers[type] || []).forEach(h => h(data));
    }

    _scheduleReconnect() {
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => {
            this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, 10000);
            this.connect();
        }, this._reconnectDelay);
    }

    close() {
        clearTimeout(this._reconnectTimer);
        this._stopHeartbeat();
        this._ws?.close();
    }
}
