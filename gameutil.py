import colorsys
import random
import time
from dataclasses import dataclass
from enum import IntEnum, auto

import yaml
import pyglet

_color_max_values = {'h': 360.0, 's': 100.0, 'v': 100.0}
BACKGROUND = pyglet.graphics.OrderedGroup(0)
FOREGROUND = pyglet.graphics.OrderedGroup(1)
CREATURES = pyglet.graphics.OrderedGroup(2)
FX = pyglet.graphics.OrderedGroup(3)
UI = pyglet.graphics.OrderedGroup(4)


def color_from_manifest(manifest):
    hsv = {'h': 0, 's': 0, 'v': 1}
    for key in 'hsv':
        if key not in manifest:
            continue

        value = manifest[key]
        if isinstance(value, list):
            value = random.randint(*value)
        hsv[key] = value / _color_max_values[key]

    converted = colorsys.hsv_to_rgb(**hsv)
    return tuple(int(channel * 255) for channel in converted)


class Tile:
    class Type(IntEnum):
        grass = auto()
        tree = auto()
        rock = auto()
        water = auto()
        wall = auto()
        door = auto()
        floor = auto()
        ground = auto()
        bush = auto()
        road = auto()

    def __init__(self):
        self.background: pyglet.sprite.Sprite = None
        self.foreground: pyglet.sprite.Sprite = None

    @classmethod
    def from_manifest(cls, manifest, batch=None, scale=1):
        background_coords = None
        if 'background' in manifest:
            background_coords = manifest['background']
        elif 'backgrounds' in manifest:
            background_coords = random.choice(manifest['backgrounds'])
        if background_coords:
            image = GameResources.data['tileset']['background'][background_coords[1], background_coords[0]]
            background_sprite = pyglet.sprite.Sprite(image, batch=batch, group=BACKGROUND)
            if scale > 1:
                background_sprite.scale = scale
            if (color := manifest.get('color')) and (background_color := color.get('background')):
                background_sprite.color = color_from_manifest(background_color)
        else:
            background_sprite = None

        foreground_coords = None
        if 'foreground' in manifest:
            foreground_coords = manifest['foreground']
        elif 'foregrounds' in manifest:
            foreground_coords = random.choice(manifest['foregrounds'])
        if foreground_coords:
            image = GameResources.data['tileset']['foreground'][foreground_coords[1], foreground_coords[0]]
            foreground_sprite = pyglet.sprite.Sprite(image, batch=batch, group=FOREGROUND)
            if scale > 1:
                foreground_sprite.scale = scale
            if (color := manifest.get('color')) and (foreground_color := color.get('foreground')):
                foreground_sprite.color = color_from_manifest(foreground_color)
        else:
            foreground_sprite = None

        tile = Tile()
        tile.background = background_sprite
        tile.foreground = foreground_sprite
        return tile

    def set_position(self, x, y):
        if self.background:
            self.background.update(x, y)
        if self.foreground:
            self.foreground.update(x, y)


class ObjectPool:
    @dataclass
    class Entry:
        time: int
        is_active: bool
        instance: object

    def __init__(self, factory, size):
        self.factory = factory
        self.pool = [self.Entry(0, False, factory()) for _ in range(size)]

    def retrieve(self):
        found = None

        for entry in self.pool:
            if not entry.is_active:
                found = entry
                break

            if found is None or entry.time < found.time:
                found = entry

        if found is None:
            found = self.Entry(0, False, self.factory())
            self.pool.append(found)

        found.is_active = True
        found.time = time.time()

        return found.instance

    def release(self, instance):
        for entry in self.pool:
            if entry.instance is instance:
                entry.is_active = False
                return


class BloodSprite(pyglet.sprite.Sprite):
    def on_animation_end(self):
        if self.visible:
            self.visible = False
            if pool := getattr(self, '_pool', None):
                pool.release(self)


@dataclass
class BattlePreparingStatus:
    kind: str
    energy: float
    active: bool


class Actor:
    def __init__(self, sprite, actor_id, x, y):
        self.sprite = sprite
        self.battle_status = None
        self.id = actor_id
        self.x = x
        self.y = y
        self.exhausted = False
        self.prepared_to_battle = False

    def prepare_to_battle(self, prepare_type, energy_amount=0):
        if prepare_type is None:
            self.prepared_to_battle = False
            if self.battle_status is not None:
                self.battle_status.visible = None
            return

        self.prepared_to_battle = True
        image = GameResources.data['icons']['attack-prepared' if prepare_type == 'attack' else 'defence-prepared']
        if self.battle_status is None:
            self.battle_status = pyglet.sprite.Sprite(
                image, self.sprite.x, self.sprite.y - 6 * 2, batch=GameResources.batch, group=UI
            )
        else:
            self.battle_status.image = image
            self.battle_status.update(self.sprite.x, self.sprite.y - 6 * 2)
            self.battle_status.visible = True

        if energy_amount <= 33:
            color = (194, 252, 93)
        elif energy_amount <= 66:
            color = (252, 218, 93)
        else:
            color = (252, 101, 93)

        self.battle_status.color = color

    def hide(self):
        self.sprite.visible = False
        if self.battle_status is not None:
            self.battle_status.visible = False


class GameResources:
    data = None
    batch = None

    @classmethod
    def load_resources(cls):
        if cls.data is not None:
            return

        cls.batch = pyglet.graphics.Batch()

        creatures_image = pyglet.resource.image('Monsters.png')
        creatures_grid = pyglet.image.ImageGrid(creatures_image, columns=19, rows=26)
        for image in creatures_grid:
            image.anchor_x = image.width // 2
            image.anchor_y = image.height // 2

        creatures = []
        for x in range(19):
            for y in range(0, 26, 2):
                creature = pyglet.image.Animation.from_image_sequence(
                    [creatures_grid[y + 1, x], creatures_grid[y, x]],
                    0.2 + 0.2 * random.random()
                )
                creatures.append(creature)

        creatures_manifest = {
            'player': {
                'sprite': 12,
                'color': (255, 255, 255)
            },
            'goblin': {
                'sprite': 72,
                'color': (68, 184, 46)
            }
        }

        terrain_image = pyglet.resource.image('Terrain.png')
        terrain_grid = pyglet.image.ImageGrid(terrain_image, columns=16, rows=11)
        for image in terrain_grid:
            image.anchor_x = image.width // 2
            image.anchor_y = image.height // 2

        terrain_objs_image = pyglet.resource.image('Terrain_Objects.png')
        terrain_objs_grid = pyglet.image.ImageGrid(terrain_objs_image, columns=19, rows=12)
        for image in terrain_objs_grid:
            image.anchor_x = image.width // 2
            image.anchor_y = image.height // 2

        with pyglet.resource.file('tileset.yml') as file:
            tileset = list(yaml.load_all(file, yaml.FullLoader))

        grouped_tileset = {}
        for tile in tileset:
            grouped_tileset.setdefault(tile['tile'], []).append(cls.process_tile_manifest(tile))

        blood_image = pyglet.resource.image('FX_Blood.png')
        blood_grid = pyglet.image.ImageGrid(blood_image, columns=14, rows=1)
        for image in blood_grid:
            image.anchor_x = image.width // 2
            image.anchor_y = image.height // 2
        blood_animations = [
            pyglet.image.Animation.from_image_sequence(sequence, 0.2)
            for sequence in (
                blood_grid[0:3], blood_grid[3:5], blood_grid[5:7],
                blood_grid[8:12], blood_grid[12:14]
            )
        ]

        sword_icon = pyglet.resource.image('sword-icon.png')
        sword_icon.anchor_x = sword_icon.width // 2
        sword_icon.anchor_y = sword_icon.height // 2

        shield_icon = pyglet.resource.image('shield-icon.png')
        shield_icon.anchor_x = shield_icon.width // 2
        shield_icon.anchor_y = shield_icon.height // 2

        bow_icon = pyglet.resource.image('bow-icon.png')
        bow_icon.anchor_x = bow_icon.width // 2
        bow_icon.anchor_y = bow_icon.height // 2

        arrow_image = pyglet.resource.image('arrow.png')
        arrow_image.anchor_x = arrow_image.width // 2
        arrow_image.anchor_y = arrow_image.height // 2

        cls.data = {
            'blood': blood_animations,
            'creatures': {
                'manifest': creatures_manifest,
                'sprites': creatures
            },
            'tileset': {
                'manifest': grouped_tileset,
                'background': terrain_grid,
                'foreground': terrain_objs_grid
            },
            'icons': {
                'attack-prepared': sword_icon,
                'defence-prepared': shield_icon,
                'shoot-prepared': bow_icon
            },
            'objects': {
                'arrow': arrow_image
            }
        }

    @staticmethod
    def process_tile_manifest(manifest):
        for key in ('backgrounds', 'foregrounds'):
            if key not in manifest:
                continue

            processed = []

            for value in manifest[key]:
                if isinstance(value, int):
                    processed.extend([None] * value)
                elif len(value) == 3:
                    processed.extend([value[:2]] * value[2])
                else:
                    processed.append(value)

            manifest[key] = processed

        return manifest
