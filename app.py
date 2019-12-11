import json
import random
import asyncio

import pyglet
from pyglet.window import key
import aiohttp

from eventloop import CustomEventLoop
from geometry import Vector
from gameutil import (
    CREATURES, FX, UI, Tile, ObjectPool, BloodSprite, Actor, GameResources,
    BattlePreparingStatus
)


class MainWindow(pyglet.window.Window):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.settings = self.load_settings()
        self.sprites_scale = 2
        GameResources.load_resources()
        self.ws_messages_queue = []

        # self.key_handler = key.KeyStateHandler()
        # self.push_handlers(self.key_handler)
        self.tiles = []
        self.player_name = self.settings['username']
        self.player = None
        self.player_stamina = 0
        self.battle_preparing = BattlePreparingStatus('', 0, False)
        self.creatures = {}
        self.ui_label = None
        self.init_ui()
        self.time = 0
        self.moving_sprites = {}
        self.rotating_sprites = {}
        self.blood_pool = ObjectPool(self._blood_factory, 10)
        self.map_width = 0
        self.map_height = 0
        pyglet.clock.schedule_interval(self.update, 1 / 60.0)

    def _blood_factory(self):
        sprite = BloodSprite(
            random.choice(GameResources.data['blood']), batch=GameResources.batch, group=FX
        )
        sprite.color = (170, 0, 0)
        sprite.visible = False

        return sprite

    def load_settings(self):
        with open('config.json', 'rt') as file:
            return json.load(file)

    def coords_to_pixels(self, x, y):
        return (
            8 * self.sprites_scale + x * 16 * self.sprites_scale,
            (self.map_height - y) * 24 * self.sprites_scale - 12 * self.sprites_scale
        )

    def init_ui(self):
        GameResources.batch.add(
            4, pyglet.gl.GL_QUADS, UI,
            ('v2f', (0, self.height, self.width, self.height, self.width, self.height - 20, 0, self.height - 20)),
            ('c4B', (0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 30, 0, 0, 0, 30))
        )
        self.ui_label = pyglet.text.Label(
            '', font_size=8, anchor_y='top', x=5, y=self.height - 5,
            batch=GameResources.batch, group=UI
        )

    def update_ui_label(self):
        if self.player:
            bps = self.battle_preparing
            if bps.active:
                bps_label = f' {"A" if bps.kind == "attack" else "D"}: {round(bps.energy, 1)}'
            else:
                bps_label = ''
            self.ui_label.document.text = (
                f'T: {self.time} '
                f'E: {self.player_stamina}{bps_label} '
                f'Pos: {self.player.x}, {self.player.y}'
            )

    def on_draw(self):
        self.update_ui_label()

        pyglet.gl.glEnable(pyglet.gl.GL_BLEND)
        pyglet.gl.glBlendFunc(pyglet.gl.GL_SRC_ALPHA, pyglet.gl.GL_ONE_MINUS_SRC_ALPHA)

        self.clear()
        GameResources.batch.draw()

    def place_on_tile(self, destination_x, destination_y, origin_x=None, origin_y=None):
        if origin_x is not None and origin_y is not None:
            origin = self.tiles[origin_y * self.map_width + origin_x]
            if origin and origin.foreground:
                origin.foreground.visible = True

        if destination_x is not None and destination_y is not None:
            destination = self.tiles[destination_y * self.map_width + destination_x]
            if destination.foreground:
                destination.foreground.visible = False

    def animate_movement(self, actor, x, y):
        if actor in self.moving_sprites:
            actor.sprite.update(*self.coords_to_pixels(actor.x, actor.y))
        velocity = (Vector(x, y) - Vector(actor.sprite.x, actor.sprite.y)).normalized * 200
        self.moving_sprites[actor] = (x, y, velocity)

    def animate_rotation(self, sprite, angle, speed=5):
        self.rotating_sprites[sprite] = (angle, speed)

    def add_creature(self, data):
        x = data['position']['x']
        y = data['position']['y']
        manifest = GameResources.data['creatures']['manifest'][data['kind']]
        coords = self.coords_to_pixels(x, y)
        sprite = pyglet.sprite.Sprite(
            GameResources.data['creatures']['sprites'][manifest['sprite']],
            x=coords[0],
            y=coords[1],
            batch=GameResources.batch, group=CREATURES
        )
        actor = Actor(sprite, data['id'], x, y)
        if self.sprites_scale > 1:
            sprite.scale = self.sprites_scale
        sprite.color = manifest['color']
        self.creatures[data['id']] = actor
        self.place_on_tile(x, y)
        if data['kind'] == 'player':
            if data['name'] == self.player_name:
                self.player = actor

    def on_game_initialized_ws_received(self, msg):
        self.map_height = height = msg['map']['height']
        self.map_width = width = msg['map']['width']
        for y in range(height):
            for x in range(width):
                tile_type = Tile.Type(msg['map']['tiles'][y * width + x]).name
                tile = Tile.from_manifest(
                    random.choice(GameResources.data['tileset']['manifest'][tile_type]),
                    GameResources.batch, self.sprites_scale
                )
                tile.set_position(*self.coords_to_pixels(x, y))
                self.tiles.append(tile)

        for creature in msg['creatures']:
            self.add_creature(creature)

    def on_player_connected_ws_received(self, msg):
        if msg['player']['id'] in self.creatures:
            return

        self.add_creature(msg['player'])

    def on_move_ws_received(self, msg):
        actor = self.creatures.get(msg['actor']['id'])
        if actor is None:
            return

        self.update_creature(actor, msg['actor'])

        if not msg['success']:
            return

        previous_x = msg['previous_position']['x']
        previous_y = msg['previous_position']['y']
        x = msg['actor']['position']['x']
        y = msg['actor']['position']['y']
        self.place_on_tile(x, y, previous_x, previous_y)
        self.animate_movement(actor, *self.coords_to_pixels(x, y))
        actor.x = x
        actor.y = y

        if self.player and msg['actor']['id'] == self.player.id:
            self.player_stamina = msg['actor']['stamina']

    def on_attack_ws_received(self, msg):
        attacker = self.creatures.get(msg['actor']['id'])
        if attacker is None:
            return

        self.update_creature(attacker, msg['actor'])

        if not msg['success']:
            return False

        defender = self.creatures.get(msg['defender']['id'])
        if defender is None:
            return

        blood: pyglet.sprite.Sprite = self.blood_pool.retrieve()
        blood.update(defender.sprite.x, defender.sprite.y)
        blood._frame_index = 0
        blood._pool = self.blood_pool
        blood.visible = True

        if not msg['defender_alive']:
            defender.hide()
            del self.creatures[msg['defender']['id']]
            self.place_on_tile(None, None, defender.x, defender.y)

    def on_prepare_to_battle_ws_received(self, msg):
        print('PREPARED')
        actor = self.creatures.get(msg['actor']['id'])
        if actor is None:
            return

        actor.prepare_to_battle(msg['subtype'], msg['energy'])

    def on_update_ws_received(self, msg):
        self.time = msg['time']

        for action in msg['actions']:
            if action['type'] == 'move':
                self.on_move_ws_received(action)
            elif action['type'] == 'attack':
                self.on_attack_ws_received(action)
            elif action['type'] == 'prepare_to_battle':
                self.on_prepare_to_battle_ws_received(action)

        for player in msg['players']:
            actor = self.creatures.get(player['id'])
            if actor is None:
                continue

            self.update_creature(actor, player)

            if player['id'] == self.player.id:
                self.player_stamina = player['stamina']

    def send_ws(self, data):
        self.ws_messages_queue.append(data)

    def update_creature(self, creature, data):
        if (exhausted := data['exhausted']) != creature.exhausted:
            self.animate_rotation(creature, 270 if exhausted else 0)
            creature.exhausted = exhausted
        if creature.prepared_to_battle and not data['prepared_to_battle']:
            creature.prepare_to_battle(None)

    def update(self, dt):
        bps = self.battle_preparing
        if bps.active:
            bps.energy += 6 * dt

        completed_move = []
        for actor, (x, y, velocity) in self.moving_sprites.items():
            current_pos = Vector(actor.sprite.x, actor.sprite.y)
            destination = Vector(x, y)
            if (destination - current_pos).magnitude_squared < 0.1:
                actor.sprite.update(*self.coords_to_pixels(actor.x, actor.y))
                completed_move.append(actor)
            else:
                current_pos = ((current_pos * (5 - 1)) + destination) / 5
                actor.sprite.update(current_pos.x, current_pos.y)

        completed_rotation = []
        for actor, (angle, speed) in self.rotating_sprites.items():
            if abs(actor.sprite.rotation - angle) < 1:
                actor.sprite.update(rotation=angle)
                completed_rotation.append(actor)
            else:
                actor.sprite.update(rotation=((actor.sprite.rotation * (speed - 1)) + angle) / speed)

        for sprite in completed_move:
            del self.moving_sprites[sprite]
        for sprite in completed_rotation:
            del self.rotating_sprites[sprite]

    def on_key_press(self, symbol, modifiers):
        if symbol == key.LEFT:
            self.send_ws({'action': 'move', 'direction': 'left'})
        elif symbol == key.UP:
            self.send_ws({'action': 'move', 'direction': 'up'})
        elif symbol == key.RIGHT:
            self.send_ws({'action': 'move', 'direction': 'right'})
        elif symbol == key.DOWN:
            self.send_ws({'action': 'move', 'direction': 'down'})
        elif symbol == key.A or symbol == key.D:
            bps = self.battle_preparing
            bps.active = True
            bps.energy = 0
            bps.kind = 'attack' if symbol == key.A else 'defence'

    def on_key_release(self, symbol, modifiers):
        if symbol == key.A or symbol == key.D:
            bps = self.battle_preparing
            if bps.active:
                bps.active = False
                self.send_ws({'action': 'prepare_to_battle', 'type': bps.kind, 'energy': int(bps.energy)})


async def receive_ws_messages(ws, window):
    async for msg in ws:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            print('Invalid JSON')
            continue

        if 'type' not in data:
            continue

        handler = getattr(window, f'on_{data["type"]}_ws_received', None)
        if handler:
            handler(data)


async def main():
    pyglet.app.event_loop = event_loop = CustomEventLoop()
    window = MainWindow(caption='Endless Compact Daemon Hunt', width=960, height=720)

    event_loop.main_window = window

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(window.settings['server']) as ws:
            event_loop.websocket_client = ws
            await asyncio.gather(event_loop.run(), receive_ws_messages(ws, window))


if __name__ == '__main__':
    pyglet.image.Texture.default_min_filter = pyglet.gl.GL_NEAREST
    pyglet.image.Texture.default_mag_filter = pyglet.gl.GL_NEAREST

    pyglet.resource.path = ['resources']
    pyglet.resource.reindex()

    asyncio.run(main())
