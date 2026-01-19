import asyncio
import websockets
import json
import random
from typing import Dict

# -------------------------
# Global game storage
# -------------------------
games: Dict[str, dict] = {}
player_connections: Dict[str, websockets.WebSocketServerProtocol] = {}

COLORS = ["red", "blue", "green", "yellow"]
VALUES = ["0","1","2","3","4","5","6","7","8","9","skip","reverse","draw2"]

# -------------------------
# Helpers
# -------------------------
def generate_game_code():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(chars) for _ in range(4))

def create_deck():
    deck = []
    for color in COLORS:
        for value in VALUES:
            deck.append({"color": color, "value": value, "id": f"{color}-{value}-1"})
            if value != "0":
                deck.append({"color": color, "value": value, "id": f"{color}-{value}-2"})

    for i in range(4):
        deck.append({"color": "wild", "value": "wild", "id": f"wild-{i}"})
        deck.append({"color": "wild", "value": "wild4", "id": f"wild4-{i}"})

    random.shuffle(deck)
    return deck

def get_next_player_index(state):
    idx = state["currentPlayerIndex"] + state["direction"]
    return idx % len(state["players"])

async def broadcast(game_code, message):
    if game_code not in games:
        return
    dead = []
    for p in games[game_code]["players"]:
        pid = p["id"]
        if pid in player_connections:
            try:
                await player_connections[pid].send(json.dumps(message))
            except:
                dead.append(pid)
    for d in dead:
        player_connections.pop(d, None)

# -------------------------
# Handlers
# -------------------------
async def handle_create_game(ws, data):
    game_code = generate_game_code()
    pid = data["playerId"]
    name = data["playerName"]

    player_connections[pid] = ws

    games[game_code] = {
        "players": [{
            "id": pid,
            "name": name,
            "isHost": True,
            "hand": [],
            "calledUno": False
        }],
        "started": False,
        "gameState": None
    }

    await ws.send(json.dumps({"type": "game_created", "gameCode": game_code}))
    await broadcast(game_code, {"type": "lobby_update","players": games[game_code]["players"]})

async def handle_join_game(ws, data):
    code = data["gameCode"]
    pid = data["playerId"]
    name = data["playerName"]

    if code not in games:
        return await ws.send(json.dumps({"type":"error","message":"Game not found"}))

    game = games[code]
    if game["started"]:
        return await ws.send(json.dumps({"type":"error","message":"Game started"}))

    player_connections[pid] = ws
    game["players"].append({
        "id": pid,
        "name": name,
        "isHost": False,
        "hand": [],
        "calledUno": False
    })

    await ws.send(json.dumps({"type":"game_joined","gameCode":code}))
    await broadcast(code, {"type":"lobby_update","players":game["players"]})

async def handle_start_game(ws, data):
    code = data["gameCode"]
    pid = data["playerId"]
    game = games.get(code)
    if not game:
        return

    if not any(p["id"] == pid and p["isHost"] for p in game["players"]):
        return

    deck = create_deck()
    for p in game["players"]:
        p["hand"] = [deck.pop() for _ in range(7)]
        p["calledUno"] = False

    start = deck.pop()
    while start["color"] == "wild":
        deck.insert(0, start)
        start = deck.pop()

    game["gameState"] = {
        "players": game["players"],
        "deck": deck,
        "discardPile": [start],
        "currentColor": start["color"],
        "currentValue": start["value"],
        "currentPlayerIndex": 0,
        "direction": 1,
        "hasDrawn": False,
        "winner": None
    }
    game["started"] = True

    await broadcast(code, {"type":"game_started","gameState":game["gameState"]})

async def handle_play_card(ws, data):
    code = data["gameCode"]
    pid = data["playerId"]
    cid = data["cardId"]
    color = data.get("chosenColor")

    game = games.get(code)
    if not game:
        return
    state = game["gameState"]

    player = state["players"][state["currentPlayerIndex"]]
    if player["id"] != pid:
        return

    card = next((c for c in player["hand"] if c["id"] == cid), None)
    if not card:
        return
    player["hand"].remove(card)
    state["discardPile"].append(card)

    state["currentColor"] = color if card["color"] == "wild" else card["color"]
    state["currentValue"] = card["value"]

    if len(player["hand"]) == 0:
        state["winner"] = player["name"]
        return await broadcast(code, {"type":"game_over","winner":player["name"]})

    if len(player["hand"]) == 1 and not player["calledUno"]:
        for _ in range(2):
            if state["deck"]:
                player["hand"].append(state["deck"].pop())

    player["calledUno"] = False

    if card["value"] == "reverse":
        state["direction"] *= -1
    elif card["value"] == "skip":
        state["currentPlayerIndex"] = get_next_player_index(state)
    elif card["value"] == "draw2":
        nxt = state["players"][get_next_player_index(state)]
        for _ in range(2):
            if state["deck"]:
                nxt["hand"].append(state["deck"].pop())

    state["currentPlayerIndex"] = get_next_player_index(state)
    state["hasDrawn"] = False

    await broadcast(code, {"type":"game_update","gameState":state})

async def handle_draw(ws, data):
    code = data["gameCode"]
    pid = data["playerId"]
    game = games.get(code)
    if not game:
        return
    state = game["gameState"]

    p = state["players"][state["currentPlayerIndex"]]
    if p["id"] != pid:
        return

    if not state["deck"]:
        top = state["discardPile"].pop()
        state["deck"] = state["discardPile"]
        random.shuffle(state["deck"])
        state["discardPile"] = [top]

    p["hand"].append(state["deck"].pop())
    state["hasDrawn"] = True

    await broadcast(code, {"type":"game_update","gameState":state})

async def handle_call_uno(ws, data):
    code = data["gameCode"]
    pid = data["playerId"]
    state = games[code]["gameState"]

    for p in state["players"]:
        if p["id"] == pid and len(p["hand"]) == 2:
            p["calledUno"] = True

    await broadcast(code, {"type":"game_update","gameState":state})

# -------------------------
# WebSocket entry
# -------------------------
async def handler(ws):
    async for msg in ws:
        data = json.loads(msg)
        t = data.get("type")
        if t == "create_game": await handle_create_game(ws, data)
        elif t == "join_game": await handle_join_game(ws, data)
        elif t == "start_game": await handle_start_game(ws, data)
        elif t == "play_card": await handle_play_card(ws, data)
        elif t == "draw_card": await handle_draw(ws, data)
        elif t == "call_uno": await handle_call_uno(ws, data)

# -------------------------
# Run server
# -------------------------
async def main():
    import os

    PORT = int(os.environ.get("PORT", 8765))
    print(f"🎴 UNO Server running on port {PORT}")

    async with websockets.serve(handler, "0.0.0.0", PORT):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())

