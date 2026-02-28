"""FastAPI entry point – HTTP routes and WebSocket handler."""

from __future__ import annotations

import json
import logging
import os
import random
import string
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.game_engine import Game
from server.models import GamePhase
from server.ws_manager import manager

logger = logging.getLogger("uvicorn.error")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Blood on the Clocktower - Auto Storyteller")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory room storage
rooms: dict[str, Game] = {}


@app.on_event("startup")
async def _on_startup():
    port = os.environ.get("BT_PORT", "8000")
    lan_ip = os.environ.get("BT_LAN_IP", "")
    logger.info("Server ready!")


def _gen_room_code() -> str:
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in rooms:
            return code


# ------------------------------------------------------------------
# Page routes
# ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/game/{room_code}", response_class=HTMLResponse)
async def game_page(room_code: str):
    return (STATIC_DIR / "game.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------
# REST API
# ------------------------------------------------------------------

@app.post("/api/rooms")
async def create_room():
    code = _gen_room_code()
    rooms[code] = Game(code)
    return {"room_code": code}


@app.get("/api/roles")
async def get_roles():
    from server.role_data import TROUBLE_BREWING
    return [
        {"id": r.id, "name": r.name_zh, "team": r.team.value, "ability": r.ability_zh}
        for r in TROUBLE_BREWING
    ]


@app.get("/api/rooms")
async def list_rooms():
    result = []
    for code, game in rooms.items():
        result.append({
            "code": code,
            "phase": game.phase.value,
            "player_count": len(game.players),
            "alive_count": game.alive_count(),
        })
    return result


@app.get("/api/rooms/{room_code}")
async def get_room(room_code: str):
    game = rooms.get(room_code)
    if not game:
        return {"error": "房间不存在"}
    return game.public_state()


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------

@app.websocket("/ws/{room_code}")
async def ws_endpoint(ws: WebSocket, room_code: str):
    await ws.accept()

    game = rooms.get(room_code)
    if not game:
        await ws.send_text(json.dumps({"type": "error", "data": {"message": "房间不存在"}}, ensure_ascii=False))
        await ws.close()
        return

    player_id: str = ""

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type", "")
            data: dict[str, Any] = msg.get("data", {})

            # --- Join ---
            if msg_type == "join":
                if game.phase.value != "lobby":
                    await ws.send_text(json.dumps({
                        "type": "join_rejected",
                        "data": {"message": "游戏已开始，无法加入。"},
                    }, ensure_ascii=False))
                    continue

                name = data.get("name", "匿名")
                player_id = data.get("player_id") or str(uuid.uuid4())[:8]
                game.add_player(player_id, name)
                manager.add(room_code, player_id, ws)

                await ws.send_text(json.dumps({
                    "type": "joined",
                    "data": {"player_id": player_id, "name": name},
                }, ensure_ascii=False))
                await manager.broadcast(room_code, "game_state", game.public_state())

            # --- Reconnect ---
            elif msg_type == "reconnect":
                player_id = data.get("player_id", "")
                if player_id in game.players:
                    manager.add(room_code, player_id, ws)
                    await ws.send_text(json.dumps({
                        "type": "reconnected",
                        "data": {"player_id": player_id},
                    }, ensure_ascii=False))
                    await manager.send_personal(room_code, player_id, "game_state", game.public_state())
                    if game.phase.value not in ("lobby",):
                        await manager.send_personal(room_code, player_id, "private_state", game.private_state(player_id))
                        for ni in game.get_player_night_info(player_id):
                            await manager.send_personal(room_code, player_id, "night_info", ni)
                else:
                    player_id = ""
                    in_progress = game.phase.value != "lobby"
                    await ws.send_text(json.dumps({
                        "type": "reconnect_failed",
                        "data": {
                            "message": "游戏已开始，无法重新加入。" if in_progress else "该房间中没有你的记录，请重新加入。",
                            "game_started": in_progress,
                        },
                    }, ensure_ascii=False))

            # --- Add bots ---
            elif msg_type == "add_bots":
                if player_id != game.host_id:
                    await ws.send_text(json.dumps({
                        "type": "error", "data": {"message": "只有房主可以添加机器人。"},
                    }, ensure_ascii=False))
                    continue
                if game.phase != GamePhase.LOBBY:
                    await ws.send_text(json.dumps({
                        "type": "error", "data": {"message": "只能在大厅阶段添加机器人。"},
                    }, ensure_ascii=False))
                    continue
                count = min(data.get("count", 1), 15 - game.player_count())
                if count <= 0:
                    await ws.send_text(json.dumps({
                        "type": "error", "data": {"message": "已达到最大玩家数。"},
                    }, ensure_ascii=False))
                    continue
                bot_names = [
                    "阿尔法", "贝塔", "伽马", "德尔塔", "艾普西隆",
                    "泽塔", "伊塔", "西塔", "约塔", "卡帕",
                    "拉姆达", "缪", "纽", "克西",
                ]
                existing_bot_count = sum(1 for p in game.players.values() if p.is_bot)
                for i in range(count):
                    idx = existing_bot_count + i
                    name = bot_names[idx] if idx < len(bot_names) else f"机器人{idx + 1}"
                    game.add_bot(name)
                await manager.broadcast(room_code, "game_state", game.public_state())

            # --- Remove bots ---
            elif msg_type == "remove_bots":
                if player_id != game.host_id:
                    continue
                if game.phase != GamePhase.LOBBY:
                    continue
                bot_ids = [pid for pid in game.seat_order if game.players[pid].is_bot]
                for bid in bot_ids:
                    game.remove_player(bid)
                await manager.broadcast(room_code, "game_state", game.public_state())

            # --- Start game ---
            elif msg_type == "start_game":
                if player_id != game.host_id:
                    await ws.send_text(json.dumps({
                        "type": "error", "data": {"message": "只有房主可以开始游戏。"},
                    }, ensure_ascii=False))
                    continue
                if game.phase != GamePhase.LOBBY:
                    continue
                if game.player_count() < 5:
                    await ws.send_text(json.dumps({
                        "type": "error", "data": {"message": "至少需要5名玩家。"},
                    }, ensure_ascii=False))
                    continue
                if game.player_count() > 15:
                    await ws.send_text(json.dumps({
                        "type": "error", "data": {"message": "最多支持15名玩家。"},
                    }, ensure_ascii=False))
                    continue
                await game.start_game()

            # --- Request private state (client missed role_assigned) ---
            elif msg_type == "request_private_state":
                if player_id and player_id in game.players and game.phase.value != "lobby":
                    await manager.send_personal(
                        room_code, player_id, "private_state", game.private_state(player_id)
                    )

            # --- Night action response ---
            elif msg_type == "action_response":
                game.submit_action(player_id, data)

            # --- Nomination ---
            elif msg_type == "nominate":
                nominee_id = data.get("nominee_id", "")
                await game.handle_nominate(player_id, nominee_id)

            # --- Vote ---
            elif msg_type == "vote":
                vote_val = data.get("vote", False)
                await game.handle_vote(player_id, vote_val)

            # --- Speech done ---
            elif msg_type == "speech_done":
                await game.handle_speech_done(player_id)

            # --- Slayer ---
            elif msg_type == "slayer_action":
                target_id = data.get("target_id", "")
                await game.handle_slayer(player_id, target_id)

            # --- Propose end nominations ---
            elif msg_type == "propose_end_nominations":
                await game.handle_propose_end_nominations(player_id)

            # --- Vote on end nominations ---
            elif msg_type == "vote_end_nominations":
                agree = data.get("agree", False)
                await game.handle_vote_end_nominations(player_id, agree)

            # --- Restart game (host, game over) ---
            elif msg_type == "restart_game":
                if player_id != game.host_id:
                    await ws.send_text(json.dumps({
                        "type": "error", "data": {"message": "只有房主可以重新开始。"},
                    }, ensure_ascii=False))
                    continue
                if game.phase != GamePhase.GAME_OVER:
                    continue
                await game.restart_game()

            # --- Propose restart vote (any player, during game) ---
            elif msg_type == "propose_restart":
                await game.handle_propose_restart(player_id)

            # --- Vote on restart ---
            elif msg_type == "vote_restart":
                agree = data.get("agree", False)
                await game.handle_vote_restart(player_id, agree)

            # --- Chat (relay to all) ---
            elif msg_type == "chat":
                text = data.get("text", "")
                sender = game.players.get(player_id)
                if sender and text:
                    await manager.broadcast(room_code, "chat", {
                        "sender_id": player_id,
                        "sender_name": sender.name,
                        "text": text,
                    })

            # --- Leave room ---
            elif msg_type == "leave":
                is_host = (player_id == game.host_id)
                if is_host:
                    if game._game_task and not game._game_task.done():
                        game._game_task.cancel()
                    await manager.broadcast(room_code, "room_closed", {"message": "房主已解散房间"})
                    rooms.pop(room_code, None)
                    player_id = ""
                    break
                elif game.phase.value == "lobby":
                    game.remove_player(player_id)
                    manager.remove(room_code, player_id)
                    await manager.broadcast(room_code, "game_state", game.public_state())
                    player_id = ""
                    break

            # --- Update seat order ---
            elif msg_type == "update_seats":
                if player_id == game.host_id:
                    new_order = data.get("seat_order", [])
                    if set(new_order) == set(game.seat_order):
                        game.seat_order = new_order
                        for i, pid in enumerate(game.seat_order):
                            game.players[pid].seat = i
                        await manager.broadcast(room_code, "game_state", game.public_state())

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if player_id:
            manager.remove(room_code, player_id)
            try:
                game_obj = rooms.get(room_code)
                if game_obj and game_obj.phase.value == "lobby":
                    game_obj.remove_player(player_id)
                    if not game_obj.seat_order:
                        rooms.pop(room_code, None)
                    else:
                        await manager.broadcast(room_code, "game_state", game_obj.public_state())
            except Exception:
                pass
